"""有界日志订阅：线程日志汇入异步事件流，重连与溢出使用缓冲快照对齐。"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any, AsyncGenerator

from core.log import LogRecord, add_listener, query_log_buffer, remove_listener


async def stream_logs() -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
    """推送缓冲快照与后续日志，慢客户端只保留有界积压。"""
    loop = asyncio.get_running_loop()
    ready = asyncio.Event()
    pending: deque[dict[str, Any]] = deque(maxlen=256)
    lock = threading.Lock()
    scheduled = False
    overflowed = False
    closed = False

    def on_log(data: dict[str, Any]) -> None:
        nonlocal scheduled, overflowed
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
        snapshot = query_log_buffer(limit=2000)
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
                snapshot = query_log_buffer(limit=2000)
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
            if len(seen) > 4000:
                seen = set(sorted(seen)[-2000:])
    finally:
        with lock:
            closed = True
            pending.clear()
        remove_listener(on_log)
