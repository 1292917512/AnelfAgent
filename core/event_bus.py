"""EventBus：轻量级异步事件总线。

参考 OpenClaw 的 Hook 系统设计，提供解耦的事件通信机制。
插件、适配器、面板等模块可订阅感兴趣的事件而无需直接依赖。

用法::

    from core.event_bus import event_bus

    # 订阅
    @event_bus.on("after_reply")
    async def log_reply(payload):
        print("Bot replied:", payload)

    # 发射
    await event_bus.emit("after_reply", {"content": "Hello!"})
"""

from __future__ import annotations

import asyncio
import traceback as _tb
from typing import Any, Awaitable, Callable, Dict, List, Optional

from core.log import log

EventHandler = Callable[[Dict[str, Any]], Awaitable[None]]


class EventBus:
    """异步事件总线，支持多处理器、优先级、一次性订阅和 owner 归属追踪。"""

    def __init__(self) -> None:
        self._handlers: Dict[str, List[_Subscription]] = {}
        self._stats: Dict[str, int] = {}

    def on(
        self,
        event: str,
        handler: Optional[EventHandler] = None,
        *,
        priority: int = 0,
        once: bool = False,
        owner: str = "",
    ) -> Callable:
        """订阅事件。可作为装饰器使用::

            @event_bus.on("after_reply")
            async def handler(payload): ...

        也可直接调用::

            event_bus.on("after_reply", my_handler, owner="plugin:weather")

        Args:
            owner: 处理器归属标识，用于 ``off_by_owner()`` 批量清理。
        """
        def _register(fn: EventHandler) -> EventHandler:
            sub = _Subscription(handler=fn, priority=priority, once=once, owner=owner)
            subs = self._handlers.setdefault(event, [])
            subs.append(sub)
            subs.sort(key=lambda s: s.priority, reverse=True)
            return fn

        if handler is not None:
            _register(handler)
            return handler
        return _register

    def off(self, event: str, handler: EventHandler) -> bool:
        """取消订阅。"""
        subs = self._handlers.get(event)
        if not subs:
            return False
        before = len(subs)
        self._handlers[event] = [s for s in subs if s.handler is not handler]
        return len(self._handlers[event]) < before

    def off_all(self, event: Optional[str] = None) -> None:
        """取消某事件（或全部事件）的所有订阅。"""
        if event:
            self._handlers.pop(event, None)
        else:
            self._handlers.clear()

    def off_by_owner(self, owner: str) -> int:
        """按 owner 批量清理所有订阅，返回移除数量。"""
        removed = 0
        for event in list(self._handlers):
            subs = self._handlers[event]
            filtered = [s for s in subs if s.owner != owner]
            removed += len(subs) - len(filtered)
            if filtered:
                self._handlers[event] = filtered
            else:
                del self._handlers[event]
        return removed

    async def emit(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """发射事件，依次调用所有已注册处理器。"""
        payload = payload or {}
        self._stats[event] = self._stats.get(event, 0) + 1

        subs = self._handlers.get(event)
        if not subs:
            return

        once_handlers: List[_Subscription] = []
        for sub in list(subs):
            try:
                await sub.handler(payload)
            except Exception:
                log(
                    f"事件处理器异常: event={event} handler={sub.handler.__name__}\n"
                    f"{_tb.format_exc()}",
                    "ERROR",
                )
            if sub.once:
                once_handlers.append(sub)

        for sub in once_handlers:
            if sub in subs:
                subs.remove(sub)

    async def emit_with_result(
        self, event: str, payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """发射事件并检查拦截。

        处理器返回 ``False`` 时中断后续调用并返回 ``False``。
        所有处理器正常完成则返回 ``True``。
        """
        payload = payload or {}
        self._stats[event] = self._stats.get(event, 0) + 1

        subs = self._handlers.get(event)
        if not subs:
            return True

        once_handlers: List[_Subscription] = []
        for sub in list(subs):
            try:
                result = await sub.handler(payload)
                if result is False:
                    return False
            except Exception:
                log(
                    f"事件处理器异常: event={event} handler={sub.handler.__name__}\n"
                    f"{_tb.format_exc()}",
                    "ERROR",
                )
            if sub.once:
                once_handlers.append(sub)

        for sub in once_handlers:
            if sub in subs:
                subs.remove(sub)

        return True

    def has_listeners(self, event: str) -> bool:
        return bool(self._handlers.get(event))

    def get_stats(self) -> Dict[str, int]:
        """返回各事件的触发次数统计。"""
        return dict(self._stats)

    def list_events(self) -> List[str]:
        """列出所有有订阅的事件名。"""
        return list(self._handlers.keys())


class _Subscription:
    __slots__ = ("handler", "priority", "once", "owner")

    def __init__(
        self,
        handler: EventHandler,
        priority: int = 0,
        once: bool = False,
        owner: str = "",
    ) -> None:
        self.handler = handler
        self.priority = priority
        self.once = once
        self.owner = owner


# 全局单例
event_bus = EventBus()

# 预定义事件名常量
EVENT_AGENT_STARTED = "agent_started"
EVENT_AGENT_STOPPED = "agent_stopped"
EVENT_MESSAGE_RECEIVED = "message_received"
EVENT_BEFORE_REPLY = "before_reply"
EVENT_AFTER_REPLY = "after_reply"
EVENT_TOOL_EXECUTED = "tool_executed"
EVENT_ERROR_OCCURRED = "error_occurred"
EVENT_CONFIG_CHANGED = "config_changed"
EVENT_PLUGIN_LOADED = "plugin_loaded"
EVENT_PLUGIN_UNLOADED = "plugin_unloaded"
EVENT_STREAM_START = "stream_start"
EVENT_STREAM_CHUNK = "stream_chunk"
EVENT_STREAM_END = "stream_end"

EVENT_ADAPTER_STARTED = "adapter_started"
EVENT_ADAPTER_STOPPED = "adapter_stopped"
EVENT_ADAPTER_MESSAGE_RECEIVED = "adapter_message_received"

# 系统级调用追踪事件
EVENT_TRACE_CALL_START = "trace_call_start"
EVENT_TRACE_CALL_END = "trace_call_end"

# 思维链路追踪事件
EVENT_THINKING_SESSION_START = "thinking_session_start"
EVENT_THINKING_SESSION_END = "thinking_session_end"
EVENT_THINKING_PHASE_CHANGE = "thinking_phase_change"
EVENT_THINKING_SITUATION = "thinking_situation"
EVENT_THINKING_DECISION = "thinking_decision"
EVENT_THINKING_CONTEXT_BUILD = "thinking_context_build"
EVENT_THINKING_LLM_START = "thinking_llm_start"
EVENT_THINKING_LLM_END = "thinking_llm_end"
EVENT_THINKING_TOOL_START = "thinking_tool_start"
EVENT_THINKING_TOOL_END = "thinking_tool_end"
EVENT_THINKING_REPLY_ROUND = "thinking_reply_round"
EVENT_THINKING_INTROSPECTION = "thinking_introspection"
EVENT_THINKING_FAKE_TOOL_CALL = "thinking_fake_tool_call"

# 多工具批量调用追踪事件
EVENT_MULTI_TOOL_PROGRESS = "multi_tool_progress"
EVENT_MULTI_TOOL_COMPLETE = "multi_tool_complete"
