"""目录镜像同步：NAS 录制单元 ↔ 音源库资源的一一对应。

同步单元：
- 文件夹（如 SafeRec 的 audio_20260806143300/）：内部音频按文件名排序经
  ffmpeg 合并为单个 16k 单声道 WAV 提交 FunASR（fsmn-vad 内部切段），
  全部片段归属该文件夹（recording_path）
- 根目录散装音频文件：按单文件单元处理

时间语义：从文件夹名解析 14 位时间戳（audio_20260806143300）作为录制基准
时间，片段 ts = 基准时间 + VAD 段内偏移，保证文本与录音时间对齐；
解析失败回退文件夹 mtime。面板按此时间归组展示，不暴露文件夹名。

镜像语义：指纹（文件清单 + 大小 + mtime 的哈希）变化 → 重处理；
NAS 删除 → 本地片段/声纹样本/登记级联删除，保持一一对应。
失败（error）单元仅在内容变化后重试；无人声单元标记 no_speech 后跳过。
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
import re
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.config import get_config, get_config_bool, get_config_float, get_config_int
from core.log import log

from . import client, ffmpeg, openlist
from .ingest import ingest_payload
from .schemas import IngestPayload, SegmentIn
from .store import VoiceprintStore, get_voiceprint_store

_DEFAULT_EXTENSIONS = ".wav,.mp3,.m4a,.flac,.ogg,.amr,.wma,.aac,.mp4,.mkv,.mov"
# OpenList 递归下钻深度上限（防御异常目录结构）
_OPENLIST_MAX_DEPTH = 4


def _extensions() -> tuple[str, ...]:
    raw = str(get_config("voiceprint_audio_extensions", _DEFAULT_EXTENSIONS)
              or _DEFAULT_EXTENSIONS)
    exts = tuple(
        e.strip().lower() if e.strip().startswith(".") else f".{e.strip().lower()}"
        for e in raw.split(",") if e.strip()
    )
    return exts or tuple(_DEFAULT_EXTENSIONS.split(","))


def _watch_dir() -> str:
    return str(get_config("voiceprint_watch_dir", "") or "").strip()


def _merge_max_seconds() -> int:
    """单批音频的最大时长（秒）：静音截断无法命中时的硬上限。"""
    return max(60, get_config_int("voiceprint_merge_max_seconds", 600))


def _merge_min_seconds() -> int:
    """单批音频的最小时长（秒）：静音截断的下限，过短的尾巴并入前一批。"""
    return max(10, get_config_int("voiceprint_merge_min_seconds", 60))


def _split_silence_db() -> float:
    """静音截断的噪音阈值（dB）：低于此音量且持续达标的区间视为可切静音。"""
    return get_config_float("voiceprint_split_silence_db", -40.0)


def _split_silence_min_s() -> float:
    """静音截断的最小静音时长（秒）：短于此的停顿不作为切点。"""
    return max(0.3, get_config_float("voiceprint_split_silence_min_s", 1.0))


def plan_splits(
    total_s: float,
    silences: List[tuple[float, float]],
    *,
    min_s: float,
    max_s: float,
) -> List[tuple[float, float]]:
    """按静音点把长音频规划为 min_s~max_s 的语义完整片段。

    优先在静音中点下刀（段落不被拦腰截断）；静音间距超 max_s 时硬切兜底；
    尾部不足 min_s 的碎片并入前一批（轻微超 max_s 可接受）。
    """
    if total_s <= 0:
        return []
    if total_s <= max_s:
        return [(0.0, total_s)]
    mids = sorted((a + b) / 2 for a, b in silences)
    cuts: List[float] = []
    last = 0.0
    for mid in mids:
        if mid - last >= min_s:
            cuts.append(mid)
            last = mid
    points = [0.0, *cuts, total_s]
    parts: List[tuple[float, float]] = []
    for a, b in zip(points, points[1:], strict=False):
        while b - a > max_s:
            parts.append((a, a + max_s))
            a += max_s
        parts.append((a, b))
    if len(parts) > 1 and parts[-1][1] - parts[-1][0] < min_s:
        tail_a, tail_b = parts.pop()
        prev_a, _ = parts.pop()
        parts.append((prev_a, tail_b))
    return parts


def _silence_skip_db() -> Optional[float]:
    """空音跳过阈值（mean_volume dB）：低于此值的文件不参与合并；>=0 关闭。"""
    value = get_config_float("voiceprint_silence_skip_db", -45.0)
    return value if value < 0 else None


def _error_retry_seconds() -> int:
    """失败单元的重试冷却（秒）：超时后即使内容未变也自动重试（自愈瞬时故障）。"""
    return max(60, get_config_int("voiceprint_error_retry_seconds", 3600))


def _source_desc() -> str:
    if openlist.is_configured():
        return f"openlist:{openlist.configured_root()}"
    watch_dir = _watch_dir()
    return f"local:{watch_dir}" if watch_dir else ""


def _exclude_patterns() -> List[str]:
    """同步排除规则（逗号分隔 glob，匹配单元完整路径或文件夹/文件名）。"""
    raw = str(get_config("voiceprint_watch_exclude", "") or "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def is_excluded(path: str) -> bool:
    """路径是否命中排除规则（命中的单元不同步、不参与镜像删除）。"""
    basename = os.path.basename(path.rstrip("/"))
    for pattern in _exclude_patterns():
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(basename, pattern):
            return True
    return False


def parse_recording_time_ns(name: str, fallback_ns: int = 0) -> int:
    """从文件夹/文件名解析录制时间（纳秒）。

    支持 14 位时间戳（audio_20260806143300）与 8 位日期（20260806），
    解析失败回退 fallback_ns（通常为 mtime）。
    """
    match = re.search(r"(\d{14})", name)
    formats = ("%Y%m%d%H%M%S",)
    if not match:
        match = re.search(r"(\d{8})", name)
        formats = ("%Y%m%d",)
    if match:
        for fmt in formats:
            try:
                return int(datetime.strptime(match.group(1), fmt).timestamp() * 1e9)
            except ValueError:
                continue
    return fallback_ns


def _fingerprint(files: List[Dict[str, Any]]) -> str:
    """录制单元内容指纹：文件名 + 大小 + mtime 的哈希。"""
    entries = sorted(f"{f['name']}:{f['size']}:{f['mtime_ns']}" for f in files)
    return hashlib.md5("|".join(entries).encode()).hexdigest()


class VoiceprintWatcher:
    """目录镜像同步引擎（懒启动后台循环 + 手动触发）。"""

    def __init__(self, store: Optional[VoiceprintStore] = None) -> None:
        self._store = store or get_voiceprint_store()
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()
        self._sync_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._last_scan_ns = 0
        self._last_result: Dict[str, Any] = {}
        self._last_error = ""
        # 正在同步的实时进度（无同步时为 None）：{current, current_started_ns, done, total}
        self._progress: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动后台扫描循环（bootstrap on_start 钩子，幂等）。"""
        await self._store.initialize()
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="voiceprint.watcher")

    async def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            if get_config_bool("voiceprint_watch_enabled", False) \
                    and not get_config_bool("voiceprint_watch_paused", False):
                try:
                    await self.sync_now()
                except Exception as exc:
                    self._last_error = str(exc)
                    log(f"目录同步异常: {exc}", "WARNING", tag="音源库")
            interval = max(10, get_config_int("voiceprint_watch_interval_seconds", 60))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=interval)
                self._wake.clear()
            except asyncio.TimeoutError:
                pass

    def status(self) -> Dict[str, Any]:
        """同步状态快照（stats/面板展示）。"""
        return {
            "enabled": get_config_bool("voiceprint_watch_enabled", False),
            "paused": get_config_bool("voiceprint_watch_paused", False),
            "source": _source_desc(),
            "running": self._task is not None,
            "syncing": self._progress is not None,
            "progress": self._progress,
            "last_scan_ns": self._last_scan_ns,
            "last_result": self._last_result,
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------------
    # 扫描主流程
    # ------------------------------------------------------------------

    async def sync_now(self) -> Dict[str, Any]:
        """执行一轮镜像同步（并发调用串行化），返回本轮摘要。"""
        if get_config_bool("voiceprint_watch_paused", False):
            return {"scanned": 0, "new": 0, "ingested": 0, "deleted": 0,
                    "failed": 0, "no_speech": 0, "paused": True,
                    "error": "同步已暂停（voiceprint_watch_paused），恢复后重试"}
        async with self._sync_lock:
            result = await self._mirror_once()
            self._last_scan_ns = time.time_ns()
            # 存副本：调用方会在返回值上追加 status 键（含 last_result），
            # 若存原对象将形成自引用循环（JSON 序列化 Circular reference）
            self._last_result = dict(result)
            self._last_error = str(result.get("error", ""))
            return result

    async def preview(self) -> Dict[str, Any]:
        """待同步预览：扫描 NAS 并与登记表 diff（只读，不处理文件）。

        Returns:
            {
                "busy": bool,          # 同步进行中（预览让路）
                "error": str,          # 来源未配置/FunASR 未配置等
                "nas_total": int,      # NAS 上的录制单元总数
                "pending": [...],      # 待同步单元（new=新增 / changed=有变更 / retry=失败重试）
                "synced": {"done": n, "no_speech": n, "error": n},
            }
        """
        if self._sync_lock.locked():
            return {"busy": True, "error": "", "nas_total": 0,
                    "pending": [], "synced": {}}
        source = _source_desc()
        if not source:
            return {"busy": False, "nas_total": 0, "pending": [], "synced": {},
                    "error": "未配置同步来源（voiceprint_watch_dir 或 OpenList）"}
        try:
            all_units = await self._discover_units()
        except Exception as exc:
            return {"busy": False, "nas_total": 0, "pending": [], "synced": {},
                    "excluded": 0, "error": f"来源扫描失败: {exc}"}

        units = [u for u in all_units if not is_excluded(u["path"])]
        excluded_count = len(all_units) - len(units)
        pending: List[Dict[str, Any]] = []
        for unit in units:
            known = await self._store.get_recording(unit["path"])
            if known and known["fingerprint"] == unit["fingerprint"]:
                if known["status"] == "error":
                    reason = "retry"
                else:
                    continue  # 已同步且未变化
            elif known:
                reason = "changed"
            else:
                reason = "new"
            pending.append({
                "path": unit["path"],
                "kind": unit["kind"],
                "started_ns": unit["started_ns"],
                "file_count": len(unit["files"]),
                "reason": reason,
            })

        synced: Dict[str, int] = {}
        for path in await self._store.list_recording_paths():
            record = await self._store.get_recording(path)
            if record:
                synced[record["status"]] = synced.get(record["status"], 0) + 1
        return {"busy": False, "error": "", "nas_total": len(units),
                "pending": pending, "synced": synced, "excluded": excluded_count}

    # ------------------------------------------------------------------
    # 删除重建（指定录制单元强制重入库）
    # ------------------------------------------------------------------

    async def rebuild(self, paths: List[str]) -> Dict[str, Any]:
        """删除指定录制单元的本地资源并立即重新处理（批量）。

        每个路径：级联删除（片段/样本/登记）→ NAS 上仍存在则重新处理入库，
        不存在则仅删除（与镜像语义一致）。排除规则内的路径拒绝重建。
        """
        if not client.is_configured():
            return {"error": "未配置 FunASR 服务（voiceprint_funasr_endpoint）",
                    "results": []}
        async with self._sync_lock:
            try:
                units = await self._discover_units()
            except Exception as exc:
                return {"error": f"来源扫描失败: {exc}", "results": []}
            unit_map = {u["path"]: u for u in units}

            results: List[Dict[str, Any]] = []
            for path in paths:
                path = path.strip()
                if not path:
                    continue
                if is_excluded(path):
                    results.append({"path": path, "outcome": "excluded",
                                    "detail": "命中同步排除规则"})
                    continue
                removed = await self._store.delete_recording(path)
                unit = unit_map.get(path)
                if not unit:
                    results.append({
                        "path": path, "outcome": "deleted",
                        "detail": f"NAS 已不存在，仅清理本地（片段 "
                                  f"{removed['segments_deleted']}）",
                    })
                    continue
                outcome = await self._process_unit(unit)
                results.append({"path": path, "outcome": outcome,
                                "detail": f"重建完成（片段 {removed['segments_deleted']} 已清理）"
                                          if outcome == "done" else ""})
            return {"error": "", "results": results}


    async def _mirror_once(self) -> Dict[str, Any]:
        if not client.is_configured():
            return {"scanned": 0, "new": 0, "ingested": 0, "deleted": 0,
                    "failed": 0, "no_speech": 0,
                    "error": "未配置 FunASR 服务（voiceprint_funasr_endpoint）"}
        units = [u for u in await self._discover_units() if not is_excluded(u["path"])]
        summary: Dict[str, Any] = {"scanned": len(units), "new": 0, "ingested": 0,
                                   "deleted": 0, "failed": 0, "no_speech": 0, "error": ""}

        # 镜像删除：登记有而 NAS 无 → 级联删除本地资源（排除规则内的路径不动）
        current_paths = {u["path"] for u in units}
        for stale in await self._store.list_recording_paths():
            if stale not in current_paths and not is_excluded(stale):
                removed = await self._store.delete_recording(stale)
                summary["deleted"] += 1
                log(f"镜像删除: {stale}（片段 {removed['segments_deleted']}，"
                    f"样本 {removed['samples_deleted']}）", tag="音源库")

        # 增量处理：新单元或指纹变化的单元
        max_per_scan = max(1, get_config_int("voiceprint_watch_max_per_scan", 50))
        processed = 0
        try:
            for unit in units:
                known = await self._store.get_recording(unit["path"])
                if known and known["fingerprint"] == unit["fingerprint"] \
                        and known["status"] != "error":
                    continue
                if known and known["status"] == "error" \
                        and known["fingerprint"] == unit["fingerprint"]:
                    # 失败单元：冷却期后自动重试（自愈服务重启等瞬时故障）
                    cooled = (time.time_ns() - int(known["synced_ns"])) / 1e9
                    if cooled < _error_retry_seconds():
                        continue
                if processed >= max_per_scan:
                    break
                processed += 1
                summary["new"] += 1
                # 实时进度：当前单元（面板轮询可见 正在同步 i/N）
                self._progress = {
                    "current": unit["path"],
                    "current_started_ns": unit["started_ns"],
                    "done": processed,
                    "total": len(units),
                }
                # 重处理前清理旧资源（保持一一对应）
                if known:
                    await self._store.delete_recording(unit["path"])
                outcome = await self._process_unit(unit)
                if outcome == "done":
                    summary["ingested"] += 1
                elif outcome == "no_speech":
                    summary["no_speech"] += 1
                else:
                    summary["failed"] += 1
        finally:
            self._progress = None
        return summary

    # ------------------------------------------------------------------
    # 单元发现（本地 / OpenList）
    # ------------------------------------------------------------------

    async def _discover_units(self) -> List[Dict[str, Any]]:
        """发现全部录制单元 [{path, kind, files, fingerprint, started_ns}]。"""
        exts = _extensions()
        if openlist.is_configured():
            return await self._discover_openlist_units(exts)
        return await self._discover_local_units(exts)

    def _make_unit(
        self, path: str, kind: str, files: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """组装录制单元；无有效音频文件返回 None。"""
        usable = [f for f in files if f["size"] > 0]
        if not usable:
            return None
        latest = max(f["mtime_ns"] for f in usable)
        return {
            "path": path,
            "kind": kind,
            "files": sorted(usable, key=lambda f: f["name"]),
            "fingerprint": _fingerprint(usable),
            "started_ns": parse_recording_time_ns(os.path.basename(path), latest),
        }

    async def _discover_local_units(self, exts: tuple[str, ...]) -> List[Dict[str, Any]]:
        watch_dir = _watch_dir()
        if not watch_dir or not os.path.isdir(watch_dir):
            return []
        recursive = get_config_bool("voiceprint_watch_recursive", True)

        def _walk() -> List[Dict[str, Any]]:
            units: List[Dict[str, Any]] = []
            # 根目录散装音频 → 单文件单元
            try:
                root_entries = sorted(os.scandir(watch_dir), key=lambda e: e.name)
            except OSError:
                return []
            for entry in root_entries:
                if entry.is_file() and entry.name.lower().endswith(exts):
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    unit = self._make_unit(entry.path, "file", [{
                        "path": entry.path, "name": entry.name,
                        "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                    }])
                    if unit:
                        units.append(unit)
            # 含音频的目录 → 文件夹单元（每个目录一个单元）
            for root, dirs, _files in os.walk(watch_dir):
                dirs.sort()
                if root == watch_dir:
                    continue
                if not recursive and os.path.dirname(root) != watch_dir:
                    dirs.clear()
                    continue
                audio_files: List[Dict[str, Any]] = []
                for name in sorted(_files):
                    if not name.lower().endswith(exts):
                        continue
                    path = os.path.join(root, name)
                    try:
                        st = os.stat(path)
                    except OSError:
                        continue
                    audio_files.append({
                        "path": path, "name": name,
                        "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                    })
                unit = self._make_unit(root, "folder", audio_files)
                if unit:
                    units.append(unit)
            return units

        return await asyncio.to_thread(_walk)

    async def _discover_openlist_units(self, exts: tuple[str, ...]) -> List[Dict[str, Any]]:
        root = openlist.configured_root()
        recursive = get_config_bool("voiceprint_watch_recursive", True)
        units: List[Dict[str, Any]] = []
        listing = await openlist.list_dir(root)
        # 根目录散装音频 → 单文件单元
        for f in listing["files"]:
            if f["name"].lower().endswith(exts):
                unit = self._make_unit(f["path"], "file", [f])
                if unit:
                    units.append(unit)
        # 子目录 → 文件夹单元（按需递归下钻）
        pending = [(d["path"], 1) for d in listing["dirs"]]
        while pending:
            dir_path, depth = pending.pop(0)
            sub = await openlist.list_dir(dir_path)
            audio = [f for f in sub["files"] if f["name"].lower().endswith(exts)]
            unit = self._make_unit(dir_path, "folder", audio)
            if unit:
                units.append(unit)
            elif recursive and depth < _OPENLIST_MAX_DEPTH and sub["dirs"]:
                pending.extend((d["path"], depth + 1) for d in sub["dirs"])
        return units

    # ------------------------------------------------------------------
    # 单元处理（下载 → 分析过滤 → 分批合并 → 转写 → 入库）
    # ------------------------------------------------------------------

    def _set_stage(self, stage: str, **extra: Any) -> None:
        """更新当前处理阶段（面板进度展示；无进度上下文时静默跳过）。"""
        if self._progress is not None:
            self._progress["stage"] = stage
            self._progress.update(extra)

    async def _analyze_files(
        self, paths: List[str],
    ) -> List[Dict[str, Any]]:
        """并发分析音频文件（时长 + 平均音量），失败的文件按可用处理（不阻断）。"""
        semaphore = asyncio.Semaphore(4)

        async def _one(path: str) -> Dict[str, Any]:
            async with semaphore:
                duration_s = 0.0
                volume: Optional[float] = None
                try:
                    duration_s = float((await ffmpeg.probe(path)).get("duration_s") or 0.0)
                except Exception as exc:
                    log(f"时长探测失败（按可用处理）: {path}: {exc}", "DEBUG", tag="音源库")
                if _silence_skip_db() is not None:
                    try:
                        volume = await ffmpeg.mean_volume_db(path)
                    except Exception as exc:
                        log(f"音量探测失败（按可用处理）: {path}: {exc}", "DEBUG", tag="音源库")
                return {"path": path, "duration_s": duration_s, "volume_db": volume}

        return await asyncio.gather(*(_one(p) for p in paths))

    async def _process_unit(self, unit: Dict[str, Any]) -> str:
        """处理单个录制单元，返回 done | no_speech | error。"""
        path = str(unit["path"])
        downloads: List[str] = []
        merged_paths: List[str] = []
        try:
            local_paths: List[str] = []
            if openlist.is_configured():
                self._set_stage("download")
                # unit["files"] 已按文件名排序，按序下载即保序
                for f in unit["files"]:
                    downloaded = await openlist.download(f["path"])
                    downloads.append(downloaded)
                local_paths = list(downloads)
            else:
                local_paths = [f["path"] for f in unit["files"]]

            # 分析：空音文件不参与合并（探测失败的按可用处理，不阻断）
            self._set_stage("analyze")
            analyzed = await self._analyze_files(local_paths)
            silence_db = _silence_skip_db()
            usable: List[Dict[str, Any]] = []
            skipped_silent = 0
            for item in analyzed:
                if silence_db is not None and item["volume_db"] is not None \
                        and item["volume_db"] < silence_db:
                    skipped_silent += 1
                    continue
                usable.append(item)

            started_ns = int(unit["started_ns"])
            base_ms = started_ns // 1_000_000
            if not usable:
                await self._store.mark_recording(
                    path, kind=unit["kind"], fingerprint=unit["fingerprint"],
                    started_ns=started_ns, file_count=len(unit["files"]),
                    status="no_speech")
                log(f"同步跳过（无人声）: {path}（空音 {skipped_silent}）", "DEBUG", tag="音源库")
                return "no_speech"

            # 合并清单（源文件 → 合并后区间，回听定位用；OpenList 存远程路径）
            local_index = {p: i for i, p in enumerate(local_paths)}
            manifest = [
                {
                    "path": unit["files"][local_index[item["path"]]]["path"],
                    "duration_s": item["duration_s"],
                }
                for item in usable if item["path"] in local_index
            ]

            # 整体合并 → 按静音点截断为 60~600s 的语义完整批次
            self._set_stage("merge")
            merged_all = await ffmpeg.merge_to_wav([f["path"] for f in usable])
            merged_paths.append(merged_all)
            try:
                total_s = float((await ffmpeg.probe(merged_all)).get("duration_s") or 0.0)
            except Exception:
                total_s = sum(f["duration_s"] for f in usable)
            silences = await ffmpeg.detect_silences(
                merged_all, noise_db=_split_silence_db(),
                min_silence_s=_split_silence_min_s())
            parts = plan_splits(total_s, silences,
                                min_s=_merge_min_seconds(),
                                max_s=_merge_max_seconds())

            all_segments: List[tuple[int, Dict[str, Any]]] = []
            for batch_index, (part_start, part_end) in enumerate(parts):
                self._set_stage("transcribe", batch=batch_index + 1, batches=len(parts))
                if len(parts) == 1:
                    part_wav = merged_all
                else:
                    fd, part_wav = tempfile.mkstemp(
                        prefix="voiceprint_part_", suffix=".wav")
                    os.close(fd)
                    await ffmpeg.split_wav(merged_all, part_start, part_end, part_wav)
                    merged_paths.append(part_wav)
                source_time = str(base_ms + int(part_start * 1000)) if base_ms else ""
                segments = await client.transcribe(part_wav, source_time=source_time)
                part_start_ms = int(part_start * 1000)
                all_segments.extend((part_start_ms, s) for s in segments)

            if not all_segments:
                await self._store.mark_recording(
                    path, kind=unit["kind"], fingerprint=unit["fingerprint"],
                    started_ns=started_ns, file_count=len(unit["files"]),
                    status="no_speech")
                log(f"同步跳过（无人声）: {path}（空音 {skipped_silent}）", "DEBUG", tag="音源库")
                return "no_speech"
            self._set_stage("ingest")
            result = await ingest_payload(IngestPayload(
                source_file=path,
                recording_path=path,
                device_source=_source_desc(),
                ts=started_ns // 1_000_000_000 or None,
                segments=[SegmentIn(
                    start_ms=s["start_ms"], end_ms=s["end_ms"],
                    text=s["text"], vector=s["vector"],
                    abs_start_ms=s.get("abs_start_ms"),
                    abs_end_ms=s.get("abs_end_ms"),
                    part_start_ms=part_ms,
                ) for part_ms, s in all_segments],
            ), store=self._store)
            await self._store.mark_recording(
                path, kind=unit["kind"], fingerprint=unit["fingerprint"],
                started_ns=started_ns, file_count=len(unit["files"]),
                status="done", segments=result.ingested)
            if manifest:
                await self._store.set_recording_files(path, manifest)
            log(f"同步入库 {result.ingested} 段: {path}"
                f"（批 {len(parts)}，空音跳过 {skipped_silent}）", tag="音源库")
            return "done"
        except Exception as exc:
            await self._store.mark_recording(
                path, kind=unit["kind"], fingerprint=unit["fingerprint"],
                started_ns=int(unit["started_ns"]), file_count=len(unit["files"]),
                status="error", error=str(exc)[:200])
            log(f"同步处理失败 [{path}]: {exc}", "WARNING", tag="音源库")
            return "error"
        finally:
            for tmp in (*merged_paths, *downloads):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


# ------------------------------------------------------------------
# 单例
# ------------------------------------------------------------------

_watcher: Optional[VoiceprintWatcher] = None


def get_voiceprint_watcher() -> VoiceprintWatcher:
    """获取 VoiceprintWatcher 单例。"""
    global _watcher
    if _watcher is None:
        _watcher = VoiceprintWatcher()
    return _watcher
