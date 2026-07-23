"""思维链路会话上下文。

会话 id 通过 ContextVar 随执行上下文传播（asyncio 子任务自动继承副本），
替代全局"当前会话"指针：并发会话交错时，每个任务发射的链路事件天然归属
自己的会话，从结构上消除错关/漏关会话的可能。

`thinking_session` 异步上下文管理器统一掌管会话生命周期：
进入时发射 SESSION_START，退出时（含异常）发射 SESSION_END。
"""

from __future__ import annotations

import contextlib
import contextvars
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from core.event_bus import (
    event_bus,
    EVENT_THINKING_SESSION_START,
    EVENT_THINKING_SESSION_END,
)

#: 当前执行上下文所属的思维链路会话 id（无会话时为 None）
current_session_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "thinking_session_id", default=None,
)


class ThinkingSessionHandle:
    """会话句柄：end 字典供会话主体填写结束信息（reason / 决策统计等）。"""

    def __init__(self, session_id: str) -> None:
        self.id = session_id
        self.end: Dict[str, Any] = {"reason": "completed"}


@contextlib.asynccontextmanager
async def thinking_session(
    payload: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[ThinkingSessionHandle]:
    """开启一个思维链路会话。

    会话 id 由本管理器生成并通过 ContextVar 传播，会话内所有嵌套调用与
    子任务的链路事件自动归属本会话；退出时（含异常）保证发射 SESSION_END。
    """
    handle = ThinkingSessionHandle(uuid.uuid4().hex[:12])
    token = current_session_id.set(handle.id)
    try:
        await event_bus.emit(
            EVENT_THINKING_SESSION_START, {**(payload or {}), "session_id": handle.id},
        )
        yield handle
    finally:
        try:
            await event_bus.emit(
                EVENT_THINKING_SESSION_END, {**handle.end, "session_id": handle.id},
            )
        finally:
            current_session_id.reset(token)


def detach_thinking_session() -> None:
    """脱离当前会话上下文。

    用于从会话中派生、但不属于该会话的独立后台任务（如心跳 tick），
    避免其链路事件被归入一个可能已结束的会话。
    """
    current_session_id.set(None)
