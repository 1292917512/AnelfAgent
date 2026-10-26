"""有界文件差异观测；只供界面展示，不改变模型输入、token 或缓存前缀。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.activity import current_activity_id
from core.event_bus import event_bus
from core.log import log
from core.stream_events import EVENT_FILE_DIFF
from entities._sdk import get_background_registry, get_current_scope
from entities.filesystem import edit_utils

MAX_CAPTURE_BYTES = 64 * 1024
MAX_DIFF_CHARS = 12_000


@dataclass(frozen=True)
class FileSnapshot:
    """写操作两侧的临时展示材料；大文件和二进制仅记录元信息。"""

    content: str = ""
    exists: bool = True
    binary: bool = False
    truncated: bool = False


def capture_file(path: str) -> FileSnapshot:
    """限量读取展示材料，观测失败不得阻止文件操作。"""
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_CAPTURE_BYTES + 1)
        binary = b"\x00" in raw[:8192]
        if binary or len(raw) > MAX_CAPTURE_BYTES:
            return FileSnapshot(binary=binary, truncated=len(raw) > MAX_CAPTURE_BYTES)
        return FileSnapshot(content=raw.decode("utf-8-sig").replace("\r\n", "\n"))
    except FileNotFoundError:
        return FileSnapshot(exists=False)
    except (OSError, UnicodeError):
        return FileSnapshot(truncated=True)


def emit_file_change(path: str, before: FileSnapshot, after: FileSnapshot, *, move_from: str = "") -> None:
    """向原执行轮次发布差异，复用主循环桥接同步工具线程。"""
    try:
        binary = before.binary or after.binary
        truncated = before.truncated or after.truncated
        diff = ""
        additions = removals = 0
        if not binary and not truncated:
            if before == after and not move_from:
                return
            diff = edit_utils.unified_diff(Path(path).name, before.content, after.content, max_chars=MAX_DIFF_CHARS)
            additions, removals = edit_utils.diff_stats(before.content, after.content)
            truncated = len(diff) > MAX_DIFF_CHARS
        operation = "renamed" if move_from else "deleted" if not after.exists else "created" if not before.exists else "modified"
        payload: dict[str, Any] = {
            "turn_id": current_activity_id.get(), "scope": get_current_scope(), "path": path,
            "move_from": move_from, "diff": diff, "additions": additions, "removals": removals,
            "binary": binary, "truncated": truncated, "operation": operation,
        }

        def dispatch() -> None:
            asyncio.create_task(event_bus.emit(EVENT_FILE_DIFF, payload))

        try:
            asyncio.get_running_loop()
            dispatch()
        except RuntimeError:
            registry = get_background_registry()
            loop = getattr(registry, "_loop", None) if registry else None
            if loop and loop.is_running():
                loop.call_soon_threadsafe(dispatch)
    except Exception as exc:
        log(f"文件差异观测失败: {exc}", "DEBUG")
