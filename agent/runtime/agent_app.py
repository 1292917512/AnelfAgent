from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional, Union

from core.event_bus import (
    event_bus,
    EVENT_AGENT_STARTED,
    EVENT_AGENT_STOPPED,
    EVENT_ERROR_OCCURRED,
    EVENT_MESSAGE_RECEIVED,
)
from agent.llm.types import ImageContent
from agent.messages import (
    Everything,
    EverythingGroup,
    MessageGroupUser,
    MessageUser,
)
from core.config import get_config, register_configs_safe
from core.log import log

# 频道内审批授权白名单：非空时仅白名单用户可 approve/deny，其他用户指令按普通消息放行。
# 条目格式："user_id"（全局）或 "channel:user_id"（限定频道）；默认空=不启用校验。
_APPROVAL_CONFIGS = {
    "审批": {
        "approval_admin_users": {
            "description": "频道内审批授权白名单（user_id 或 channel:user_id），空=不限制",
            "default": [],
        },
    },
}
register_configs_safe(_APPROVAL_CONFIGS)

# 白名单为空时仅提示一次（启动后首次遇到审批指令时）
_approval_admin_hint_logged = False


class AgentStatus(str, Enum):
    """智能体运行状态。"""

    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PROCESSING = "processing"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass(slots=True)
class AgentEvent:
    """统一运行时事件：由适配器/系统注入，供 AgentApp 消费处理。"""

    type: str  # "message" | "notice" | "command" | ...
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentStats:
    """运行时统计信息。"""

    start_time: float = 0.0
    message_count: int = 0
    error_count: int = 0
    last_message_time: float = 0.0
    last_error: str = ""

    @property
    def uptime(self) -> float:
        if self.start_time <= 0:
            return 0.0
        return time.time() - self.start_time


class AgentApp:
    """
    AnelfAgent 统一智能体运行时入口。

    集成 Mind/Storage/LLM/Tools 等，所有适配器（NoneBot/FastAPI/CLI）
    统一通过 submit() 或 send_message() 提交输入。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._status = AgentStatus.STOPPED
        self._stats = AgentStats()
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        self._handler: Optional[Callable[[AgentEvent], Awaitable[None]]] = None
        self._runtime = None

    @property
    def runtime(self):
        if self._runtime is None:
            from agent.runtime.singleton import get_runtime
            self._runtime = get_runtime()
        return self._runtime

    @property
    def status(self) -> AgentStatus:
        return self._status

    @property
    def stats(self) -> AgentStats:
        return self._stats

    def set_handler(self, handler: Callable[[AgentEvent], Awaitable[None]]) -> None:
        """设置自定义事件处理器（覆盖默认的内置处理）。"""
        self._handler = handler

    async def start(self) -> None:
        if self._running:
            return
        self._main_loop = asyncio.get_running_loop()
        self._status = AgentStatus.STARTING
        self._running = True
        self._stats.start_time = time.time()
        self._task = asyncio.create_task(self._run_loop(), name="agent.agent_core.AgentApp")
        self._status = AgentStatus.RUNNING
        await event_bus.emit(EVENT_AGENT_STARTED, {"time": self._stats.start_time})

    async def stop(self) -> None:
        if not self._running:
            return
        self._status = AgentStatus.STOPPING
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._status = AgentStatus.STOPPED
        await event_bus.emit(EVENT_AGENT_STOPPED, {"uptime": self._stats.uptime})

    # ------------------------------------------------------------------
    # 输入接口
    # ------------------------------------------------------------------

    async def submit(self, event: AgentEvent) -> None:
        """向运行时提交通用事件（在当前事件循环中执行）。"""
        await self._ensure_started()
        await self._queue.put(event)

    async def send_message(
        self,
        *,
        user_id: Union[int, str],
        content: str,
        user_name: str = "",
        group_id: Union[int, str] = 0,
        to_me: bool = False,
        nickname: str = "",
        images: Optional[list[ImageContent]] = None,
        media_segments: Optional[list] = None,
        adapter_key: str = "",
        message_id: str = "",
        session_id: str = "",
        reply_to_id: str = "",
        reply_content: str = "",
        trigger_mind: bool = True,
    ) -> None:
        """便捷方法：提交一条消息事件（适配器推荐使用此方法）。

        当调用方与 AgentApp 不在同一事件循环时（如 Telegram 独立线程），
        自动使用 run_coroutine_threadsafe 进行跨循环线程安全提交。
        """
        resolved_message_id = message_id or uuid.uuid4().hex[:16]
        resolved_session_id = session_id or (str(group_id) if group_id not in (0, "0", "") else str(user_id))

        payload: dict[str, Any] = {
            "user_id": user_id,
            "content": content,
            "user_name": user_name,
            "group_id": group_id,
            "to_me": to_me,
            "nickname": nickname,
            "adapter_key": adapter_key,
            "message_id": resolved_message_id,
            "session_id": resolved_session_id,
            "reply_to_id": reply_to_id,
            "reply_content": reply_content,
            "trigger_mind": trigger_mind,
        }
        if images:
            payload["images"] = images
        if media_segments:
            payload["media_segments"] = media_segments

        event = AgentEvent(type="message", payload=payload)
        await self._ensure_started()

        # 检测跨循环调用（如 Telegram 独立线程 → 主循环）
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if (
            self._main_loop is not None
            and current_loop is not None
            and self._main_loop is not current_loop
            and self._main_loop.is_running()
        ):
            asyncio.run_coroutine_threadsafe(self._queue.put(event), self._main_loop)
        else:
            await self._queue.put(event)

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    def get_status_info(self) -> dict[str, Any]:
        """返回当前运行时状态摘要。"""
        mind_phase = "unknown"
        try:
            if self._runtime is not None:
                mind_phase = self._runtime.mind.phase.value
        except Exception:
            pass
        return {
            "status": self._status.value,
            "mind_phase": mind_phase,
            "uptime": round(self._stats.uptime, 1),
            "message_count": self._stats.message_count,
            "error_count": self._stats.error_count,
            "last_message_time": self._stats.last_message_time,
            "last_error": self._stats.last_error,
            "queue_size": self._queue.qsize(),
        }

    # ------------------------------------------------------------------
    # 内部处理
    # ------------------------------------------------------------------

    async def _ensure_started(self) -> None:
        if self._running:
            return
        self._main_loop = asyncio.get_running_loop()
        self._running = True
        self._stats.start_time = time.time()
        self._status = AgentStatus.RUNNING
        self._task = asyncio.create_task(self._run_loop(), name="agent.agent_core.AgentApp")
        await event_bus.emit(EVENT_AGENT_STARTED, {"time": self._stats.start_time})

    async def _run_loop(self) -> None:
        while self._running:
            event = await self._queue.get()
            prev_status = self._status
            self._status = AgentStatus.PROCESSING
            try:
                if self._handler:
                    await self._handler(event)
                else:
                    await self._default_handler(event)
            except Exception as exc:
                self._stats.error_count += 1
                self._stats.last_error = str(exc)
                self._status = AgentStatus.ERROR
                log(f"AgentApp 处理事件异常: {event.type} -> {exc}", "ERROR")
                await event_bus.emit(EVENT_ERROR_OCCURRED, {"error": str(exc), "event_type": event.type})
            finally:
                self._queue.task_done()
                # ERROR 状态保留到下一个事件到来再被覆盖，便于外部观测最近一次失败
                if self._status == AgentStatus.PROCESSING:
                    self._status = AgentStatus.RUNNING

    async def _default_handler(self, event: AgentEvent) -> None:
        """默认事件处理：将 message 事件转为 Everything 并交给 Respond。"""
        if event.type == "message":
            payload = event.payload
            self._stats.message_count += 1
            self._stats.last_message_time = time.time()

            await event_bus.emit(EVENT_MESSAGE_RECEIVED, payload)

            # 批准回复路由：approve/deny <request_id> 直接决策，不触发思维
            if await _try_resolve_approval(payload):
                return

            anything = _build_message_everything(payload)
            anything.set_text_content(str(payload.get("content", "")))
            await self.runtime.respond.accept_data(anything)
        else:
            log(f"未处理的事件类型: {event.type}", "DEBUG")


def _is_approval_admin(user_id: str, channel_id: str) -> bool:
    """判断用户是否有权在频道内做出审批决策。

    白名单（approval_admin_users）为空时不启用校验，任何用户均可审批
    （保持向后兼容）；非空时仅 ``user_id`` 或 ``channel:user_id`` 命中者生效。
    """
    global _approval_admin_hint_logged
    admins = get_config("approval_admin_users", []) or []
    if not isinstance(admins, (list, tuple)):
        admins = []
    if not admins:
        if not _approval_admin_hint_logged:
            log("approval_admin_users 未配置，频道内审批不限制操作者（任意用户可审批）",
                "WARNING", tag="权限")
            _approval_admin_hint_logged = True
        return True
    return user_id in admins or f"{channel_id}:{user_id}" in admins


async def _try_resolve_approval(payload: dict) -> bool:
    """把频道内的批准回复（approve/deny <id>）路由到批准管理器。

    仅当 request_id 对应挂起会话时拦截消息，否则按普通消息放行。
    """
    content = str(payload.get("content", "") or "").strip()
    if not content or len(content) > 64:
        return False
    from agent.approval.renderer import parse_approval_command
    parsed = parse_approval_command(content)
    if not parsed:
        return False
    decision_str, request_id = parsed

    from agent.approval import get_approval_gate, get_approval_manager
    manager_gate = get_approval_gate()
    session = await get_approval_manager().get_session(request_id)
    if session is None or not session.is_pending():
        return False

    user_id = str(payload.get("user_id", "") or "unknown")
    channel_id = str(payload.get("adapter_key", "") or "")
    if not _is_approval_admin(user_id, channel_id):
        # 非授权用户：审批指令按普通消息放行（不拦截、不决策）
        log(f"频道内审批指令来自非授权用户，已按普通消息放行: "
            f"{request_id} (user={user_id}, channel={channel_id})", "WARNING", tag="权限")
        return False
    ok = await (manager_gate.approve(request_id, decided_by=user_id)
                if decision_str == "approved"
                else manager_gate.deny(request_id, decided_by=user_id))
    if ok:
        # 决策确认回执（best-effort）
        try:
            from agent.channel.manager import get_channel_manager
            channel = get_channel_manager().get(str(payload.get("adapter_key", "") or ""))
            if channel is not None:
                mark = "✅ 已批准" if decision_str == "approved" else "🚫 已拒绝"
                send_text = getattr(channel, "send_text", None)
                if callable(send_text):
                    await send_text(str(payload.get("group_id") or payload.get("user_id") or ""),
                                    f"{mark}: {session.request.tool_name} ({request_id})")
        except Exception:
            pass
        log(f"频道内批准决策: {request_id} -> {decision_str} (by {user_id})", tag="权限")
        return True
    return False


# 全局单例
_agent_app: Optional[AgentApp] = None


def get_agent_app() -> AgentApp:
    global _agent_app
    if _agent_app is None:
        _agent_app = AgentApp()
    return _agent_app


def _build_message_everything(payload: dict[str, Any]) -> Everything:
    """从 payload 构建 Everything 消息对象。"""
    user_id = payload.get("user_id", 0)
    group_id = payload.get("group_id", 0)
    user_name = payload.get("user_name", "")
    to_me = payload.get("to_me", False)
    nickname = payload.get("nickname", "")
    images: list[ImageContent] = payload.get("images") or []
    media_segments: list = payload.get("media_segments") or []
    adapter_key: str = payload.get("adapter_key", "")
    message_id: str = payload.get("message_id", "")
    session_id: str = payload.get("session_id", "")
    reply_to_id: str = payload.get("reply_to_id", "")
    reply_content: str = payload.get("reply_content", "")
    trigger_mind: bool = payload.get("trigger_mind", True)

    if group_id and group_id not in (0, "0", ""):
        msg = MessageGroupUser(
            uid=user_id,
            group_id=group_id,
            user_name=user_name,
            to_me=to_me,
            nickname=nickname,
            images=images,
            media_segments=media_segments,
            adapter_key=adapter_key,
            adapter_message_id=message_id,
            session_id=session_id,
            reply_to_id=reply_to_id,
            reply_content=reply_content,
            trigger_mind=trigger_mind,
        )
    else:
        msg = MessageUser(  # type: ignore[assignment]
            uid=user_id,
            user_name=user_name,
            images=images,
            media_segments=media_segments,
            adapter_key=adapter_key,
            adapter_message_id=message_id,
            session_id=session_id,
            reply_to_id=reply_to_id,
            reply_content=reply_content,
            trigger_mind=trigger_mind,
        )
    return msg
