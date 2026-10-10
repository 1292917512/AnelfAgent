from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional, Union

from agent.llm.types import ImageContent
from agent.messages import (
    Everything,
    MessageGroupUser,
    MessageUser,
)
from core.event_bus import (
    EVENT_AGENT_STARTED,
    EVENT_AGENT_STOPPED,
    EVENT_CHAT_BROADCAST,
    EVENT_ERROR_OCCURRED,
    EVENT_MESSAGE_RECEIVED,
    event_bus,
)
from core.log import log

if TYPE_CHECKING:
    from agent.runtime.runtime import AgentRuntime

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


_EVENT_QUEUE_MAX_SIZE = 10000


class AgentApp:
    """
    AnelfAgent 统一智能体运行时入口。

    集成 Mind/Storage/LLM/Tools 等，所有适配器（FastAPI/CLI 等）
    统一通过 submit() 或 send_message() 提交输入。
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=_EVENT_QUEUE_MAX_SIZE)
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._status = AgentStatus.STOPPED
        self._stats = AgentStats()
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

        self._handler: Optional[Callable[[AgentEvent], Awaitable[None]]] = None
        self._runtime: Optional[AgentRuntime] = None

    @property
    def runtime(self) -> AgentRuntime:
        if self._runtime is None:
            from agent.runtime.singleton import require_runtime
            self._runtime = require_runtime()
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
                pass  # 取消属正常关闭流程（正常控制流，非异常）
            self._task = None
        self._status = AgentStatus.STOPPED
        await event_bus.emit(EVENT_AGENT_STOPPED, {"uptime": self._stats.uptime})

    # ------------------------------------------------------------------
    # 输入接口
    # ------------------------------------------------------------------

    async def submit(self, event: AgentEvent) -> None:
        """向运行时提交通用事件（在当前事件循环中执行）。"""
        await self._ensure_started()
        self._enqueue(event)

    def _enqueue(self, event: AgentEvent) -> None:
        """非阻塞入队；满载时拒绝新请求并保留已受理事件。"""
        if self._queue.full():
            raise RuntimeError("消息队列已满，请稍后重试")
        self._queue.put_nowait(event)

    async def send_message(
        self,
        *,
        user_id: Union[int, str],
        content: str,
        user_name: str = "",
        group_id: Union[int, str] = 0,
        to_me: bool = False,
        images: Optional[list[ImageContent]] = None,
        media_segments: Optional[list] = None,
        adapter_key: str = "",
        message_id: str = "",
        session_id: str = "",
        reply_to_id: str = "",
        reply_content: str = "",
        trigger_mind: bool = True,
        message_kind: str = "chat",
    ) -> None:
        """便捷方法：提交一条消息事件（适配器推荐使用此方法）。

        当调用方与 AgentApp 不在同一事件循环时（如 Telegram 独立线程），
        自动使用 run_coroutine_threadsafe 进行跨循环线程安全提交。
        """
        resolved_message_id = message_id or uuid.uuid4().hex[:16]
        # 多会话 chat_id：调用方显式提供时作为 entity_scope 后缀（实现多会话隔离）；
        # 未提供时回退到旧行为：群聊 scope 用 group_id 区分，单聊为空。
        # 注意：旧 webui 历史曾把 user_id 兜底为 session_id 导致 scope=user_{uid}#{uid}，
        # 兼容起见 webui 频道未带 chat_id 时返回空（scope=user_{uid}），前端默认 chat 复用该 scope。
        if session_id:
            resolved_session_id = session_id
        elif group_id not in (0, "0", ""):
            resolved_session_id = str(group_id)
        else:
            resolved_session_id = ""

        payload: dict[str, Any] = {
            "user_id": user_id,
            "content": content,
            "user_name": user_name,
            "group_id": group_id,
            "to_me": to_me,
            "adapter_key": adapter_key,
            "message_id": resolved_message_id,
            "session_id": resolved_session_id,
            "reply_to_id": reply_to_id,
            "reply_content": reply_content,
            "trigger_mind": trigger_mind,
            "message_kind": message_kind,
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
            future = asyncio.run_coroutine_threadsafe(self.submit(event), self._main_loop)
            await asyncio.wrap_future(future)
        else:
            self._enqueue(event)

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
            log("get_status_info 异常已忽略", "DEBUG")
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
                if event.type == "message" and event.payload.get("adapter_key") == "webui":
                    await event_bus.emit(EVENT_CHAT_BROADCAST, {
                        "event": "message_failed",
                        "message_id": event.payload.get("message_id", ""),
                        "chat_id": event.payload.get("session_id") or "default",
                    })
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


            anything = _build_message_everything(payload)
            anything.set_text_content(str(payload.get("content", "")))
            await self.runtime.pipeline.ingest(anything)
        else:
            log(f"未处理的事件类型: {event.type}", "DEBUG")


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
    images: list[ImageContent] = payload.get("images") or []
    media_segments: list = payload.get("media_segments") or []
    adapter_key: str = payload.get("adapter_key", "")
    message_id: str = payload.get("message_id", "")
    session_id: str = payload.get("session_id", "")
    reply_to_id: str = payload.get("reply_to_id", "")
    reply_content: str = payload.get("reply_content", "")
    trigger_mind: bool = payload.get("trigger_mind", True)
    message_kind: str = payload.get("message_kind", "chat")

    if group_id and group_id not in (0, "0", ""):
        msg = MessageGroupUser(
            uid=user_id,
            group_id=group_id,
            user_name=user_name,
            to_me=to_me,
            images=images,
            media_segments=media_segments,
            adapter_key=adapter_key,
            adapter_message_id=message_id,
            session_id=session_id,
            reply_to_id=reply_to_id,
            reply_content=reply_content,
            trigger_mind=trigger_mind,
            message_kind=message_kind,
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
            message_kind=message_kind,
        )
    return msg
