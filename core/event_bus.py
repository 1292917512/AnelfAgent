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

# 单个事件处理器的执行超时（秒），超时记 WARNING 且不影响其他处理器
HANDLER_TIMEOUT_SECONDS = 5.0


def _handler_name(handler: Callable[..., Any]) -> str:
    """处理器的可读名称（partial / 可调用对象无 __name__ 时回退 repr）。"""
    return getattr(handler, "__qualname__", None) or repr(handler)


def _same_handler(a: Callable[..., Any], b: Callable[..., Any]) -> bool:
    """判断两个处理器是否指向同一回调（兼容绑定方法）。

    绑定方法每次属性访问都会生成新对象，``is`` 比较永远失败，
    需比较其 __self__ 与 __func__。
    """
    if a is b:
        return True
    a_self = getattr(a, "__self__", None)
    b_self = getattr(b, "__self__", None)
    if a_self is not None and b_self is not None:
        return a_self is b_self and getattr(a, "__func__", None) is getattr(b, "__func__", None)
    return False


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
        self._handlers[event] = [s for s in subs if not _same_handler(s.handler, handler)]
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

    async def _invoke_handler(
        self, event: str, sub: "_Subscription", payload: Dict[str, Any],
    ) -> Any:
        """执行单个处理器（带超时保护），返回其结果。

        超时/异常记 WARNING 并返回 None，不影响其他处理器。
        """
        try:
            return await asyncio.wait_for(sub.handler(payload), timeout=HANDLER_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            log(
                f"事件处理器超时 ({HANDLER_TIMEOUT_SECONDS}s): "
                f"event={event} handler={_handler_name(sub.handler)}",
                "WARNING",
            )
        except Exception:
            log(
                f"事件处理器异常: event={event} handler={_handler_name(sub.handler)}\n"
                f"{_tb.format_exc()}",
                "WARNING",
            )
        return None

    def _prune_once(
        self, subs: List["_Subscription"], batch: List["_Subscription"],
    ) -> None:
        """从订阅列表中移除已触发的 once 订阅。"""
        for sub in batch:
            if sub.once and sub in subs:
                subs.remove(sub)

    async def emit(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """发射事件，并发调用所有已注册处理器（各自带超时保护）。"""
        payload = payload or {}
        self._stats[event] = self._stats.get(event, 0) + 1

        subs = self._handlers.get(event)
        if not subs:
            return

        # 快照当前订阅列表，避免处理器执行期间注册/退订导致迭代错乱；
        # off() 会整体替换列表对象，once 清理必须作用于当前列表而非旧快照
        batch = list(subs)
        await asyncio.gather(*(self._invoke_handler(event, sub, payload) for sub in batch))
        current = self._handlers.get(event)
        if current is not None:
            self._prune_once(current, batch)

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

        batch = list(subs)
        try:
            for sub in batch:
                result = await self._invoke_handler(event, sub, payload)
                if result is False:
                    return False
        finally:
            self._prune_once(subs, batch)

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
EVENT_CHAT_BROADCAST = "chat_broadcast"
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

# 界面交互命令（entities/ui 工具 → 前端工作台）
EVENT_UI_COMMAND = "ui_command"

# 分享链接创建（entities/share → 聊天 SSE → 前端分享卡片）
EVENT_SHARE_CREATED = "share_created"

# ------------------------------------------------------------------
# Plan 模式（present_plan / update_goal 工具 → 前端 plan 浮窗与卡片）
# ------------------------------------------------------------------
# 计划提交：Agent 调 present_plan 工具时立即发射（不走 ApprovalGate），
# 前端收到后插入 PlanCard + 弹出 PlanPanel 浮窗
EVENT_PLAN_SUBMITTED = "plan_submitted"
# 计划步骤状态变化：update_goal 工具调用成功后发射（pending/in_progress/completed/skipped）
EVENT_PLAN_STEP_UPDATED = "plan_step_updated"
# 计划整体状态变化（active/completed/cancelled）
EVENT_PLAN_STATUS_CHANGED = "plan_status_changed"
# 用户从前端取消计划（浮窗按钮 → 后端 interrupt scope）
EVENT_PLAN_CANCELLED = "plan_cancelled"
# 计划被删除（delete_goal 工具）：前端移除对应 PlanCard/浮窗记录
EVENT_PLAN_DELETED = "plan_deleted"

# ------------------------------------------------------------------
# 子代理（delegate_task 工具 → 前端 delegation 卡片）
# ------------------------------------------------------------------
EVENT_DELEGATION_STARTED = "delegation_started"
EVENT_DELEGATION_PROGRESS = "delegation_progress"
EVENT_DELEGATION_RESOLVED = "delegation_resolved"

