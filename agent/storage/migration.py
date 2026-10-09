"""重启迁移日志：运行期预拷贝，独占启动窗口最终同步后切换位置。"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from threading import RLock
from typing import Callable, Literal

from pydantic import BaseModel

from core.config import ConfigManager
from core.file_utils import atomic_write_text, walk_files

_lock = RLock()


class PendingMigration(BaseModel):
    """迁移原位置、目标位置及已预拷贝文件的持久化凭据。"""

    kind: Literal["data", "volume"]
    source: str
    target: str
    volume_id: str = ""
    files: list[str]
    notes_only: bool = False


def _marker() -> Path:
    return Path(ConfigManager._get_config_file()).resolve().parent / "pending-migration.json"


def pending_migration() -> PendingMigration | None:
    path = _marker()
    if not path.exists():
        return None
    return PendingMigration.model_validate_json(path.read_text(encoding="utf-8"))


def stage_migration(job: PendingMigration) -> None:
    """只登记意图，不修改任何运行期路径；已有迁移时拒绝重叠提交。"""
    with _lock:
        if pending_migration() is not None:
            raise RuntimeError("已有待重启迁移，请重启完成后再操作")
        atomic_write_text(_marker(), job.model_dump_json(indent=2))


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as stream:
        is_sqlite = stream.read(16) == b"SQLite format 3\x00"
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".migration-", delete=False) as tmp:
        temporary = Path(tmp.name)
    try:
        if is_sqlite:
            src_db = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
            try:
                dest_db = sqlite3.connect(temporary)
                try:
                    src_db.backup(dest_db)
                finally:
                    dest_db.close()
            finally:
                src_db.close()
        else:
            shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    if is_sqlite:
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(target) + suffix).unlink(missing_ok=True)


def finalize_pending_migration(assign_volume: Callable[[str, str], None]) -> None:
    """仅由组合根在取得实例锁后、任何存储打开前调用；失败阻止启动。"""
    with _lock:
        job = pending_migration()
        if job is None:
            return
        source, target = Path(job.source).resolve(), Path(job.target).resolve()
        if source == target or source.is_relative_to(target) or target.is_relative_to(source):
            raise ValueError("迁移源与目标不得重叠")
        if not source.exists():
            raise FileNotFoundError(f"迁移源已丢失: {source}")
        if job.kind == "data" and os.environ.get("ANELF_DATA_DIR", "").strip():
            raise RuntimeError("待迁移数据目录与 ANELF_DATA_DIR 冲突，请移除覆盖后重启")
        if source.is_file():
            _copy_file(source, target)
        else:
            files = walk_files(source, ("-wal", "-shm"))
            if job.notes_only:
                files = [path for path in files if (
                    path.parent == source and path.suffix == ".md"
                ) or path.relative_to(source).parts[0] in ("events", "groups", "profile_backups")]
            current = {path.relative_to(source).as_posix() for path in files}
            # 先记下本次可能写入的文件，失败重试也能清理已从源删除的旧副本。
            owned = set(job.files) | current
            job.files = sorted(owned)
            atomic_write_text(_marker(), job.model_dump_json(indent=2))
            for path in files:
                dest = (target / path.relative_to(source)).resolve()
                if not dest.is_relative_to(target):
                    raise ValueError("迁移目标内存在越界符号链接")
                _copy_file(path, dest)
            for name in owned - current:
                stale = (target / name).resolve()
                if not stale.is_relative_to(target):
                    raise ValueError("迁移文件清单越界")
                stale.unlink(missing_ok=True)
        if job.kind == "data":
            ConfigManager.set_persisted({"data_root": str(target)})
        else:
            assign_volume(job.volume_id, str(target))
        _marker().unlink()
