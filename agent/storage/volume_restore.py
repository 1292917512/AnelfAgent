"""存储卷恢复交换 — 重启落盘的待恢复标记消费。

恢复流程分两段：Web 端校验备份 + 暂存产物并写入 pending 标记
（services.volume_ops），随后触发重启；进程重启后 bootstrap 的
init_storage（最早的存储节点，任何连接打开前）调用
``consume_pending_restores`` 完成文件交换——此刻所有数据文件必然
已关闭，无需写排空机制。

交换语义按卷形态区分：
- SQLITE：暂存文件就位（现文件留 ``.pre-restore-<ts>.bak``），
  并清除旧库遗留的 ``-wal``/``-shm`` 侧文件（防止旧 WAL 回放进新库）
- COGNEE_TREE：整树替换（解包 → 现树改名保留 → 就位）
- NOTES_TREE：选择性覆盖（便签树路径即数据根，仅覆盖备份涵盖的
  条目：根级 *.md + events/ + groups/ + profile_backups/，
  data/ cognee/ backups/ 等无关条目原样保留）
"""

from __future__ import annotations

import json
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def stage_restore_entry(volume_id: str, kind: VolumeKind, staged_path: str) -> None:
    """登记一条待恢复条目（services 侧调用；幂等追加）。"""
    root = backups_root()
    root.mkdir(parents=True, exist_ok=True)
    marker = pending_marker_path()
    entries: List[Dict[str, str]] = []
    if marker.is_file():
        try:
            entries = list(json.loads(marker.read_text(encoding="utf-8")).get("entries", []))
        except (OSError, ValueError):
            entries = []
    entries.append({
        "volume_id": volume_id,
        "kind": kind.value,
        "staged_path": staged_path,
        "requested_at": f"{time.time():.3f}",
    })
    marker.write_text(
        json.dumps({"entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_pending_entries() -> List[Dict[str, Any]]:
    marker = pending_marker_path()
    if not marker.is_file():
        return []
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
        entries = raw.get("entries", [])
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []
    except (OSError, ValueError) as exc:
        log(f"待恢复标记读取失败: {exc}", "ERROR", tag="存储卷")
        return []


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
            registry.get(volume_id)  # 未知卷（登记缺失）直接判失败
            target = Path(registry.resolve_path(volume_id))
            staged = Path(staged_path)
            if not staged.exists():
                raise FileNotFoundError(f"暂存产物不存在: {staged}")
            if kind_raw == VolumeKind.SQLITE.value:
                _swap_sqlite(staged, target)
            elif kind_raw == VolumeKind.COGNEE_TREE.value:
                _swap_tree(staged, target, whole_replace=True)
            elif kind_raw == VolumeKind.NOTES_TREE.value:
                _swap_tree(staged, target, whole_replace=False)
            else:
                raise ValueError(f"未知卷形态: {kind_raw}")
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
        marker.write_text(
            json.dumps({"entries": failed}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    elif marker.exists():
        marker.unlink()
    return results


# ----------------------------------------------------------------------
# 交换实现
# ----------------------------------------------------------------------


def _backup_suffix() -> str:
    return f".pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"


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
    if target.exists():
        target.rename(target.with_name(f"{target.name}{_backup_suffix()}"))
        _prune_pre_restore(target)
    # 旧库遗留的 WAL/SHM 侧文件必须清除：SQLite 会把旧 WAL 回放进新库
    for side in (f"{target}-wal", f"{target}-shm", f"{target}-journal"):
        Path(side).unlink(missing_ok=True)
    shutil.move(str(staged), str(target))


def _extract_tar(staged: Path) -> Path:
    """解包 tgz 到暂存兄弟目录，返回解包根（内容相对树根存放）。"""
    extract_root = staged.with_name(f"{staged.stem}.extracted")
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)
    with tarfile.open(staged, "r:gz") as tar:
        tar.extractall(extract_root, filter="data")
    return extract_root


def _swap_tree(staged: Path, target: Path, *, whole_replace: bool) -> None:
    extracted = _extract_tar(staged)
    ts = _backup_suffix()
    if whole_replace:
        if target.exists():
            target.rename(target.with_name(f"{target.name}{ts}"))
            _prune_pre_restore(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted), str(target))
        staged.unlink(missing_ok=True)
        return
    # 便签树：仅覆盖备份涵盖的条目，数据根其余内容原样保留
    target.mkdir(parents=True, exist_ok=True)
    for item in sorted(extracted.iterdir()):
        dest = target / item.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        shutil.move(str(item), str(dest))
    extracted.rmdir()
    staged.unlink(missing_ok=True)


def pending_summary() -> Optional[List[str]]:
    """待恢复卷 id 列表（供状态接口展示）。"""
    entries = read_pending_entries()
    return [str(e.get("volume_id", "")) for e in entries] or None
