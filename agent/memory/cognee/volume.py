"""Cognee 卷策略：占用快照、提交恢复点筛选和协调器静默窗口。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from core.file_utils import walk_files
from core.log import log

from .storage import cognee_storage_stats

_T = TypeVar("_T")


async def observe_size(root: Path) -> int:
    """复用后台统计，请求路径不遍历原生数据库。"""
    stats = await cognee_storage_stats.get(str(root))
    return int(stats.get("total_bytes", 0))


def backup_members(root: Path) -> list[Path]:
    """干净工作区省略可再生保护快照；脏工作区必须保留完整恢复现场。"""
    clean = False
    journal = root / ".publication" / "state.json"
    if journal.exists():
        try:
            commit = json.loads(journal.read_text(encoding="utf-8"))
            clean = isinstance(commit, dict) and commit.get("version") == 1 and commit.get("dirty") is False
        except (OSError, ValueError):
            log("Cognee 提交记录无法确认，备份保留恢复点", "WARNING")
    return [path for path in walk_files(root) if not (
        path.relative_to(root).parts[0] == ".publication"
        and (clean or path.name in {"owner.lock", "worker.lock"})
    )]


async def run_snapshot(
    job: Callable[[], Awaitable[_T]], check: Callable[[], None] | None = None,
) -> tuple[_T, str]:
    """文件枚举与读取都在同一静默窗口内完成，保留安全取消检查。"""
    from .runtime import get_cognee_coordinator

    coordinator = get_cognee_coordinator()
    if coordinator is None:
        return await job(), "live"
    quiesced = coordinator.is_running
    result = await coordinator.run_in_idle_window(job(), check_cancel=check)
    return result, "idle_window" if quiesced else "live"
