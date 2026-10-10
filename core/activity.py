"""执行过程的轮次归属与生命周期事件，不参与模型上下文或追踪开关。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import AsyncIterator, Iterator

from core.event_bus import event_bus
from core.log import current_log_actor
from core.tool_context import request_trace

EVENT_ACTIVITY_STARTED = "activity_started"
EVENT_ACTIVITY_FINISHED = "activity_finished"
ACTIVITY_TEXT_LIMIT = 16000
current_activity_id: ContextVar[str] = ContextVar("activity_id", default="")
current_activity_owner: ContextVar[dict[str, str] | None] = ContextVar("activity_owner", default=None)


@contextmanager
def activity_owner(*, kind: str, label: str, owner_id: str = "", scope: str = "") -> Iterator[None]:
    """为任务及嵌套执行声明可展示的身份，随异步调用树传递。"""
    token = current_activity_owner.set({"kind": kind, "label": label, "owner_id": owner_id, "origin_scope": scope})
    try:
        yield
    finally:
        current_activity_owner.reset(token)


@dataclass
class ActivityHandle:
    """执行轮次的最终状态。"""

    status: str = "completed"
    error: str = ""


@asynccontextmanager
async def activity_scope(turn_id: str, scope: str, *, label: str = "", kind: str = "reflection") -> AsyncIterator[ActivityHandle]:
    """为并行及嵌套执行绑定独立轮次，并在所有退出路径发出终态。"""
    parent_id = current_activity_id.get()
    token = current_activity_id.set(turn_id)
    handle = ActivityHandle()
    try:
        await event_bus.emit(EVENT_ACTIVITY_STARTED, {
            "turn_id": turn_id, "scope": scope,
            "origin_scope": request_trace().get("scope", scope),
            "parent_id": parent_id, "actor": current_log_actor(),
            "label": label, "kind": kind, **(current_activity_owner.get() or {}),
        })
        yield handle
    except asyncio.CancelledError:
        handle.status = "cancelled"
        raise
    except BaseException as exc:
        handle.status = "failed"
        handle.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            await event_bus.emit(EVENT_ACTIVITY_FINISHED, {"turn_id": turn_id, "status": handle.status, "error": handle.error})
        finally:
            current_activity_id.reset(token)
