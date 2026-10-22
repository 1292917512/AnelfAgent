"""存储卷恢复交换 — 重启落盘的待恢复标记消费。

恢复流程分两段：Web 端校验备份 + 暂存产物并写入 pending 标记
（services.volume_ops），随后触发重启；进程重启后 bootstrap 的
init_storage（最早的存储节点，任何连接打开前）调用
``consume_pending_restores`` 完成文件交换——此刻所有数据文件必然
已关闭，无需写排空机制。

交换语义按卷形态区分：
- SQLITE：暂存文件就位（现文件及侧文件留在 ``.pre-restore-*`` 目录），
  并清除旧库遗留的 ``-wal``/``-shm`` 侧文件（防止旧 WAL 回放进新库）
- COGNEE_TREE：整树替换（解包 → 现树改名保留 → 就位）
- NOTES_TREE：选择性覆盖（便签树路径即数据根，仅覆盖备份涵盖的
  条目：根级 *.md + events/ + groups/ + profile_backups/，
  data/ cognee/ backups/ 等无关条目原样保留）
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Optional

from core.file_utils import atomic_write_text
from core.log import log
from core.storage_volume import VolumeKind, get_volume_registry

PENDING_RESTORE_MARKER = ".pending-restore.json"
_PRE_RESTORE_KEEP = 3  # 每卷保留的 pre-restore 安全副本数

# 配置注册（备份保留策略，services.volume_ops 消费）
_VOLUME_CONFIGS = {
    "storage/backup": {
        "volume_backup_retention": {
            "description": "每个存储卷自动保留的备份份数（新备份完成后自动清理最旧的超额备份；0 = 不自动清理）",
            "default": 5,
            "min": 0,
            "max": 100,
            "unit": "份",
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_VOLUME_CONFIGS)


def backups_root() -> Path:
    """卷备份根目录：<数据目录>/backups/volumes。"""
    from core.path import ConfigPaths, project_root

    path = Path(ConfigPaths.MEMORY_DIR) / "backups" / "volumes"
    if not path.is_absolute():
        path = Path(project_root()) / path
    return path


def pending_marker_path() -> Path:
    return backups_root() / PENDING_RESTORE_MARKER


def stage_restore_entry(volume_id: str, kind: VolumeKind, staged_path: str, *, sha256: str = "") -> None:
    """登记一条待恢复条目（services 侧调用；幂等追加）。"""
    root = backups_root()
    root.mkdir(parents=True, exist_ok=True)
    marker = pending_marker_path()
    entries: List[Dict[str, str]] = []
    if marker.is_file():
        try:
            entries = list(json.loads(marker.read_text(encoding="utf-8")).get("entries", []))
        except (OSError, ValueError) as exc:
            raise RuntimeError("待恢复标记损坏，拒绝覆盖") from exc
    if any(entry.get("volume_id") == volume_id for entry in entries):
        raise RuntimeError(f"卷 {volume_id} 已有待恢复任务")
    entries.append({
        "volume_id": volume_id,
        "kind": kind.value,
        "staged_path": staged_path,
        "requested_at": f"{time.time():.3f}",
        "sha256": sha256,
    })
    _write_marker_atomic(marker, {"entries": entries})


def _write_marker_atomic(marker: Path, payload: Dict[str, Any]) -> None:
    """tmp 文件 + os.replace 原子写标记（写一半崩溃不会留下损坏 JSON
    导致全部待恢复条目静默蒸发）。"""
    atomic_write_text(marker, json.dumps(payload, ensure_ascii=False, indent=2))


def read_pending_entries() -> List[Dict[str, Any]]:
    marker = pending_marker_path()
    if not marker.is_file():
        return []
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
        entries = raw["entries"]
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise ValueError("恢复条目格式错误")
        return entries
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("待恢复标记无法读取，拒绝跳过恢复") from exc


def consume_pending_restores() -> List[Dict[str, Any]]:
    """消费全部待恢复条目（bootstrap init_storage 最早调用）。

    成功的条目从标记移除；失败的条目保留在标记中（下次重启重试），
    单条失败不影响其余条目。
    """
    entries = read_pending_entries()
    if not entries:
        return []
    registry = get_volume_registry()
    failed: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    for entry in entries:
        volume_id = str(entry.get("volume_id", ""))
        kind_raw = str(entry.get("kind", ""))
        staged_path = str(entry.get("staged_path", ""))
        try:
            descriptor = registry.get(volume_id)
            if descriptor.kind.value != kind_raw:
                raise ValueError("待恢复卷形态不符")
            target = Path(registry.resolve_path(volume_id))
            staged = Path(staged_path)
            if not staged.exists():
                raise FileNotFoundError(f"暂存产物不存在: {staged}")
            if entry.get("sha256"):
                with staged.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                        raise ValueError("恢复暂存产物校验和不符")
            if kind_raw == VolumeKind.SQLITE.value:
                _swap_sqlite(staged, target)
            elif kind_raw == VolumeKind.COGNEE_TREE.value:
                _swap_tree(staged, target, whole_replace=True)
            elif kind_raw == VolumeKind.NOTES_TREE.value:
                _swap_tree(staged, target, whole_replace=False)
            else:
                raise ValueError(f"未知卷形态: {kind_raw}")
            # 先从持久化标记确认消费，再删除暂存产物；崩溃后允许安全重试。
            remaining = [item for item in entries if item is not entry]
            _write_marker_atomic(pending_marker_path(), {"entries": remaining})
            entries = remaining
            try:
                staged.unlink(missing_ok=True)
            except OSError as exc:
                log(f"已恢复产物清理失败: {exc}", "WARNING", tag="存储卷")
            results.append({"volume_id": volume_id, "ok": True, "target": str(target)})
            log(
                f"存储卷恢复完成: {volume_id} -> {target}（原数据保留为 .pre-restore 副本）",
                tag="存储卷",
            )
        except Exception as exc:
            failed.append(entry)
            results.append({"volume_id": volume_id, "ok": False, "error": str(exc)})
            log(f"存储卷恢复失败（条目保留待下次重试）: {volume_id}: {exc}", "ERROR", tag="存储卷")
    marker = pending_marker_path()
    if failed:
        _write_marker_atomic(marker, {"entries": failed})
    elif marker.exists():
        marker.unlink()
    return results


# ----------------------------------------------------------------------
# 交换实现
# ----------------------------------------------------------------------


def _backup_suffix() -> str:
    return f".pre-restore-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}"


def _prune_pre_restore(target: Path) -> None:
    """控制 .pre-restore-* 安全副本数量（保留最新 N 份）。"""
    candidates = sorted(target.parent.glob(f"{target.name}.pre-restore-*"))
    for old in candidates[:-_PRE_RESTORE_KEEP]:
        try:
            if old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
            else:
                old.unlink(missing_ok=True)
        except OSError:
            log(f"pre-restore 副本清理失败: {old}", "DEBUG", tag="存储卷")


def _swap_sqlite(staged: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    candidate = target.with_name(f".{target.name}.restore-{time.time_ns()}")
    # bootstrap 尚未打开连接；原库及 WAL 一并保留，即使旧库损坏也能恢复。
    # 目标文件在同文件系统内原子替换，跨卷暂存复制失败不会触碰现库。
    previous = target.with_name(f"{target.name}{_backup_suffix()}")
    originals = [path for path in (target, Path(f"{target}-wal"), Path(f"{target}-shm"),
                                   Path(f"{target}-journal")) if path.exists()]
    backed_up = False
    try:
        _durable_copy(staged, candidate)
        previous.mkdir()
        for path in originals:
            _durable_copy(path, previous / path.name)
        backed_up = True
        try:
            for side in (f"{target}-wal", f"{target}-shm", f"{target}-journal"):
                Path(side).unlink(missing_ok=True)
            os.replace(candidate, target)
        except BaseException:
            for path in originals:
                _durable_copy(previous / path.name, path)
            raise
    finally:
        candidate.unlink(missing_ok=True)
        if not backed_up and previous.exists():
            shutil.rmtree(previous)
    _prune_pre_restore(target)


def _durable_copy(source: Path, target: Path) -> None:
    shutil.copyfile(source, target)
    with target.open("r+b") as stream:
        os.fsync(stream.fileno())
    shutil.copystat(source, target)


def validate_archive(
    staged: Path, *, notes_only: bool = False, check: Callable[[], None] = lambda: None,
) -> None:
    """完整读取成员，拒绝路径越界、链接、设备及便签范围外的条目。"""
    with tarfile.open(staged, "r:gz") as tar:
        for member in tar:
            check()
            path = PurePosixPath(member.name)
            if (not path.parts or path.is_absolute() or ".." in path.parts
                    or "\\" in member.name or ":" in member.name
                    or not (member.isfile() or member.isdir())):
                raise ValueError(f"不安全的归档成员: {member.name}")
            if notes_only and not (
                path.parts[0] in {"events", "groups", "profile_backups"}
                or (len(path.parts) == 1 and path.suffix == ".md" and member.isfile())
            ):
                raise ValueError(f"归档超出便签卷范围: {member.name}")
            if member.isfile():
                stream = tar.extractfile(member)
                if stream is None:
                    raise ValueError(f"无法读取归档成员: {member.name}")
                with stream:
                    while stream.read(1024 * 1024):
                        check()
        # tar 的结束标记可能早于 gzip 尾部，继续读取以验证 CRC/截断。
        while tar.fileobj.read(1024 * 1024):
            check()


def _extract_tar(staged: Path, *, notes_only: bool = False, root: Path | None = None) -> Path:
    """解包 tgz 到暂存兄弟目录，返回解包根（内容相对树根存放）。"""
    validate_archive(staged, notes_only=notes_only)
    extract_root = root or staged.with_name(f"{staged.stem}.extracted-{time.time_ns()}")
    extract_root.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(staged, "r:gz") as tar:
            tar.extractall(extract_root, filter="data")
        for path in extract_root.rglob("*"):
            if path.is_file():
                with path.open("rb" if os.name != "nt" else "r+b") as stream:
                    os.fsync(stream.fileno())
    except BaseException:
        shutil.rmtree(extract_root)
        raise
    return extract_root


def _swap_tree(staged: Path, target: Path, *, whole_replace: bool) -> None:
    extracted = _extract_tar(staged, notes_only=not whole_replace,
                             root=target.with_name(f".{target.name}.restore-{time.time_ns()}"))
    ts = _backup_suffix()
    if whole_replace:
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = target.with_name(f"{target.name}{ts}")
        if target.exists():
            target.rename(previous)
        try:
            extracted.rename(target)
        except BaseException:
            if previous.exists() and not target.exists():
                previous.rename(target)
            raise
        finally:
            if extracted.exists():
                shutil.rmtree(extracted)
        _prune_pre_restore(target)
        return
    # 便签树：仅覆盖备份涵盖的条目，数据根其余内容原样保留
    target.mkdir(parents=True, exist_ok=True)
    previous = target.with_name(f"{target.name}{ts}")
    previous.mkdir()
    installed: list[Path] = []
    try:
        for item in sorted(extracted.iterdir()):
            dest = target / item.name
            if dest.exists():
                dest.rename(previous / item.name)
            item.rename(dest)
            installed.append(dest)
    except BaseException:
        for dest in reversed(installed):
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        for old in previous.iterdir():
            old.rename(target / old.name)
        raise
    finally:
        shutil.rmtree(extracted)
    _prune_pre_restore(target)


def pending_summary() -> Optional[List[str]]:
    """待恢复卷 id 列表（供状态接口展示）。"""
    entries = read_pending_entries()
    return [str(e.get("volume_id", "")) for e in entries] or None
