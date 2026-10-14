"""有界日志订阅：线程日志汇入异步事件流，重连与溢出使用缓冲快照对齐。"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any, AsyncGenerator

from core.log import LogRecord, add_listener, query_log_buffer, remove_listener


async def stream_logs(*, debug: bool = False, limit: int = 200) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """按订阅级别推送有界快照与增量，普通模式仅接收警告及错误。"""
    levels = None if debug else frozenset({"WARNING", "ERROR", "CRITICAL"})
    limit = max(1, min(limit, 2000))
    loop = asyncio.get_running_loop()
    ready = asyncio.Event()
    pending: deque[dict[str, Any]] = deque(maxlen=256)
    lock = threading.Lock()
    scheduled = False
    overflowed = False
    closed = False

    def on_log(data: dict[str, Any]) -> None:
        nonlocal scheduled, overflowed
        if levels is not None and data["level"] not in levels:
            return
        with lock:
            if closed:
                return
            overflowed = overflowed or len(pending) == pending.maxlen
            pending.append(data)
            if scheduled:
                return
            scheduled = True
            loop.call_soon_threadsafe(ready.set)

    add_listener(on_log)
    try:
        snapshot = query_log_buffer(levels=levels, limit=limit)
        seen = {entry["seq"] for entry in snapshot}
        yield "snapshot", {"logs": snapshot}
        while True:
            try:
                await asyncio.wait_for(ready.wait(), timeout=25)
            except asyncio.TimeoutError:
                yield "ping", {}
                continue
            with lock:
                batch = list(pending)
                pending.clear()
                resync = overflowed
                overflowed = False
                scheduled = False
                ready.clear()
            if resync:
                snapshot = query_log_buffer(levels=levels, limit=limit)
                seen = {entry["seq"] for entry in snapshot}
                yield "snapshot", {"logs": snapshot}
                continue
            for entry in sorted(batch, key=lambda value: value["seq"]):
                if entry["seq"] in seen:
                    continue
                seen.add(entry["seq"])
                record = LogRecord(
                    level=entry["level"], message=entry["message"], tag=entry.get("tag"),
                    timestamp=entry["timestamp"], seq=entry["seq"],
                )
                yield "log", record.as_dict()
            if len(seen) > limit * 2:
                seen = set(sorted(seen)[-limit:])
    finally:
        with lock:
            closed = True
            pending.clear()
        remove_listener(on_log)
