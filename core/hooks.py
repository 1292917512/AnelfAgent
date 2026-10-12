"""钩子的执行模式、身份与有界观测；调度由各执行宿主管理。

模型可见：本模块只发布过程事件，不向模型注入消息。
Token 影响：无；调用方按需通过工具结果或动态上下文提供业务结果。
缓存影响：不参与上下文组装，不修改任何缓存前缀。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import AsyncIterator

from core.activity import current_activity_id
from core.conversation_scope import current_scope
from core.event_bus import event_bus
from core.sanitizer import sanitize_text

EVENT_HOOK_EXECUTION = "hook_execution"


class HookMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"


@dataclass
class HookRun:
    id: str
    name: str
    event: str
    mode: HookMode
    source: str
    scope: str
    turn_id: str
    started_at: float
    status: str = "running"
    duration_ms: int = 0
    detail: str = ""


class HookJournal:
    """保留进行中与最近完成的钩子执行，不持久化消息或脚本内容。"""

    def __init__(self, limit: int = 100) -> None:
        self._recent: deque[dict[str, object]] = deque(maxlen=limit)
        self._active: dict[str, HookRun] = {}

    def snapshot(self) -> dict[str, object]:
        return {"active": [asdict(run) for run in self._active.values()],
                "recent": list(reversed(self._recent))}

    def clear(self) -> None:
        self._recent.clear()
        self._active.clear()


hook_journal = HookJournal()


@asynccontextmanager
async def hook_execution(name: str, event: str, mode: HookMode, *, source: str = "internal",
                         scope: str = "") -> AsyncIterator[HookRun]:
    """记录实际执行的起止、耗时与异常，取消信号原样传递。"""
    run = HookRun(uuid.uuid4().hex, name, event, mode, source,
                  scope or current_scope(), current_activity_id.get(), time.time())
    started = time.monotonic()
    hook_journal._active[run.id] = run
    try:
        await event_bus.emit(EVENT_HOOK_EXECUTION, asdict(run))
        yield run
        if run.status == "running":
            run.status = "done"
    except asyncio.CancelledError:
        run.status = "cancelled"
        raise
    except BaseException as exc:
        run.status = "error"
        run.detail = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        run.duration_ms = max(0, round((time.monotonic() - started) * 1000))
        run.detail = sanitize_text(run.detail)[:1200]
        hook_journal._active.pop(run.id, None)
        record = asdict(run)
        hook_journal._recent.append(record)
        await event_bus.emit(EVENT_HOOK_EXECUTION, record)
