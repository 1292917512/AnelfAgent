"""HTTP API 频道 — 内嵌 uvicorn 的通用 HTTP 通信接口。

继承 BaseChannel，声明 SEND_TEXT 能力。
启动后自动监听 HTTP 端口：POST /api/chat 同步问答（async_mode 立即返回），
GET /api/chat/stream 订阅该用户的回复流式帧（delta / tool_call / reply / turn_end）。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus, _err, _ok
from agent.channel.schemas import (
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    SendRequest,
    SendResponse,
)
from agent.llm.types import ImageContent
from core.log import log

from .config import HttpApiConfig

# ------------------------------------------------------------------
# Request / Response
# ------------------------------------------------------------------

class ImageItem(BaseModel):
    url: str = ""
    base64: str = ""
    mime_type: str = "image/jpeg"


class ChatRequest(BaseModel):
    message: str
    user_id: str = "api_user"
    user_name: str = ""
    group_id: str = ""
    session_id: str = ""
    message_id: str = ""
    reply_to_id: str = ""
    to_me: bool = True
    images: List[ImageItem] = Field(default_factory=list)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    # 立即返回不等待回复（回复经 /api/chat/stream 流式帧投递）
    async_mode: bool = False


class ChatResponse(BaseModel):
    request_id: str
    status: str = "ok"
    reply: str = ""
    error: str = ""


class _StreamSub:
    """单个 SSE 订阅者（scope 前缀过滤 + 帧队列）。"""

    __slots__ = ("prefix", "queue")

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.queue: asyncio.Queue[str] = asyncio.Queue()


# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------


class HttpApiChannel(BaseChannel[HttpApiConfig]):
    """HTTP API 频道。"""

    _entity_description = "HTTP 接口通信频道"

    metadata = ChannelMetadata(
        name="HTTP API",
        description="内嵌 uvicorn 的通用 HTTP 通信频道",
        version="1.0.0",
        author="AnelfAgent",
    )
    _Configs = HttpApiConfig

    def __init__(self) -> None:
        self._pending_replies: Dict[str, asyncio.Future[str]] = {}
        self._server: Optional[Any] = None
        self._server_task: Optional[asyncio.Task[None]] = None
        self._stream_subs: List[_StreamSub] = []
        super().__init__()

    channel_id = "http_api"

    display_name = "HTTP 接口"

    display_order = 40

    capabilities: Set[ChannelCapability] = {ChannelCapability.SEND_TEXT}

    async def start(self) -> None:
        import uvicorn

        host: str = self.config.host
        port: int = int(self.config.port)

        if not self._is_loopback(host) and not self.config.api_token:
            raise RuntimeError(
                f"HTTP API 频道监听非回环地址 {host} 但未配置 api_token，"
                "拒绝启动（外部请求将无法被认证）。请配置 api_token 或将 host 改为 127.0.0.1"
            )

        app = self._create_app()
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())
        self._subscribe_stream_events()
        self._status = ChannelStatus.RUNNING
        log(f"HTTP API 频道已启动: http://{host}:{port}（认证: {'token' if self.config.api_token else '仅回环'}）")

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._server_task:
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, KeyboardInterrupt):
                log("stop 异常已忽略", "DEBUG")
            self._server_task = None
        from core.event_bus import event_bus
        event_bus.off_by_owner("channel:http_api")
        for sub in self._stream_subs:
            sub.queue.put_nowait("")  # 唤醒流生成器退出
        self._stream_subs.clear()
        for fut in self._pending_replies.values():
            if not fut.done():
                fut.cancel()
        self._pending_replies.clear()
        self._status = ChannelStatus.STOPPED
        log("HTTP API 频道已停止")

    # ------------------------------------------------------------------
    # 流式过程事件订阅（内核事件 → SSE 帧；机器消费面，不进对话历史）
    # ------------------------------------------------------------------

    def _subscribe_stream_events(self) -> None:
        """订阅思维流式事件并转发给匹配的 SSE 订阅者（同 webui 频道的事件面）。"""
        from core.event_bus import (
            EVENT_AFTER_REPLY,
            EVENT_THINKING_TOOL_END,
            EVENT_THINKING_TOOL_START,
            event_bus,
        )
        from core.stream_events import EVENT_ASSISTANT_DELTA
        event_bus.on(EVENT_ASSISTANT_DELTA, self._on_assistant_delta, owner="channel:http_api")
        event_bus.on(EVENT_THINKING_TOOL_START, self._on_tool_start, owner="channel:http_api")
        event_bus.on(EVENT_THINKING_TOOL_END, self._on_tool_end, owner="channel:http_api")
        event_bus.on(EVENT_AFTER_REPLY, self._on_after_reply, owner="channel:http_api")

    def _push_frame(self, frame: Dict[str, Any], scope: str) -> None:
        """按 scope 前缀过滤推送帧到订阅者队列（无订阅者零开销）。"""
        if not self._stream_subs or not scope:
            return
        data = json.dumps(frame, ensure_ascii=False)
        for sub in self._stream_subs:
            if scope == sub.prefix or scope.startswith(sub.prefix + "#"):
                sub.queue.put_nowait(data)

    async def _on_assistant_delta(self, payload: dict) -> None:
        self._push_frame({
            "type": "delta",
            "scope": str(payload.get("scope", "")),
            "turn_id": str(payload.get("turn_id", "")),
            "delta": str(payload.get("delta", "")),
            "reasoning": bool(payload.get("reasoning")),
        }, str(payload.get("scope", "")))

    async def _on_tool_start(self, payload: dict) -> None:
        self._push_frame({
            "type": "tool_call",
            "scope": str(payload.get("scope", "")),
            "call_id": str(payload.get("tool_id", "")),
            "name": str(payload.get("tool_name", "")),
            "status": "running",
            "arguments": payload.get("arguments_preview", ""),
        }, str(payload.get("scope", "")))

    async def _on_tool_end(self, payload: dict) -> None:
        self._push_frame({
            "type": "tool_call",
            "scope": str(payload.get("scope", "")),
            "call_id": str(payload.get("tool_id", "")),
            "name": str(payload.get("tool_name", "")),
            "status": "done" if payload.get("success") else "error",
            "result_preview": payload.get("result_preview", "") or payload.get("error", ""),
            "duration_ms": payload.get("duration_ms", 0),
        }, str(payload.get("scope", "")))

    async def _on_after_reply(self, payload: dict) -> None:
        self._push_frame({
            "type": "turn_end",
            "scope": str(payload.get("scope", "")),
            "error": bool(payload.get("error")),
        }, str(payload.get("scope", "")))

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """回复 HTTP API 调用方（pending future 或流式订阅者二选一）。"""
        fut = self._pending_replies.pop(chat_id, None)
        if fut and not fut.done():
            fut.set_result(text)
            return _ok({"chat_id": chat_id})
        # async 模式：无 pending future 时经 SSE reply 帧投递最终文本
        if self._deliver_to_subscribers(chat_id, {"type": "reply", "content": text}):
            return _ok({"chat_id": chat_id, "delivery": "stream"})
        return _err(f"无待回复请求: chat_id={chat_id}")

    def _deliver_to_subscribers(self, chat_id: str, payload: Dict[str, Any]) -> bool:
        """向匹配该 chat 的 SSE 订阅者推送一帧，返回是否有订阅者送达。"""
        candidates = (
            f"user_{self.channel_id}:{chat_id}",
            f"group_{self.channel_id}:{chat_id}",
        )
        data = json.dumps(payload, ensure_ascii=False)
        delivered = False
        for sub in list(self._stream_subs):
            if any(
                p == sub.prefix or p.startswith(sub.prefix + "#") or sub.prefix.startswith(p + "#")
                for p in candidates
            ):
                sub.queue.put_nowait(data)
                delivered = True
        return delivered

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _is_loopback(host: str) -> bool:
        return host in ("127.0.0.1", "localhost", "::1")

    def _check_auth(self, request: Request) -> bool:
        """校验 API Token（未配置时仅允许回环来源，启动时已强制）。"""
        token = self.config.api_token
        if not token:
            return True
        provided = request.headers.get("x-api-token", "")
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        return bool(provided) and hmac.compare_digest(provided, token)

    def _expect_reply(self, reply_key: str) -> asyncio.Future[str]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        # 同 key 已有挂起请求（同用户并发调用）：立即失败旧 future——
        # 否则旧请求干等超时，且 Agent 回复会错配给新请求
        old = self._pending_replies.get(reply_key)
        if old is not None and not old.done():
            old.set_exception(RuntimeError("该会话已有更新的请求，本请求被取代"))
        self._pending_replies[reply_key] = fut
        return fut

    def _create_app(self) -> FastAPI:
        from agent.runtime.agent_app import get_agent_app

        timeout: int = int(self.config.reply_timeout)

        fastapi_app = FastAPI(title="AnelfAgent HTTP API", version="1.0.0")
        adapter = self

        @fastapi_app.post("/api/chat", response_model=ChatResponse)
        async def chat(req: ChatRequest, request: Request) -> ChatResponse:
            if not adapter._check_auth(request):
                return ChatResponse(
                    request_id=req.request_id,
                    status="error",
                    error="未认证：缺少或错误的 API Token",
                )
            agent_app = get_agent_app()
            reply_key = req.user_id if not req.group_id else req.group_id
            session_id = req.session_id or (req.group_id if req.group_id else req.user_id)
            message_id = req.message_id or req.request_id
            fut = None if req.async_mode else adapter._expect_reply(reply_key)

            images: List[ImageContent] = []
            for img in req.images:
                if img.url:
                    images.append(ImageContent(data=img.url, is_url=True))
                elif img.base64:
                    images.append(ImageContent(data=img.base64, mime_type=img.mime_type))

            await agent_app.send_message(
                user_id=req.user_id,
                content=req.message,
                user_name=req.user_name or req.user_id,
                group_id=req.group_id if req.group_id else 0,
                to_me=req.to_me,
                images=images or None,
                adapter_key=adapter.channel_id,
                message_id=message_id,
                session_id=session_id,
                reply_to_id=req.reply_to_id,
            )

            # async 模式：投递完成即返回，最终回复经 /api/chat/stream 的
            # reply / turn_end 帧投递（长任务不受 reply_timeout 约束）
            if fut is None:
                return ChatResponse(request_id=req.request_id, status="accepted")

            try:
                reply = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                adapter._pending_replies.pop(reply_key, None)
                return ChatResponse(
                    request_id=req.request_id,
                    status="timeout",
                    error=f"Agent 回复超时（{timeout}s）",
                )

            return ChatResponse(request_id=req.request_id, reply=reply)

        @fastapi_app.get("/api/chat/stream")
        async def chat_stream(user_id: str, request: Request):
            """SSE 流端点：推送该 user 的 delta / tool_call / reply / turn_end 帧。"""
            from sse_starlette.sse import EventSourceResponse

            sub = _StreamSub(f"user_{adapter.channel_id}:{user_id}")
            adapter._stream_subs.append(sub)

            async def event_generator():
                try:
                    while True:
                        if await request.is_disconnected():
                            break
                        try:
                            data = await asyncio.wait_for(sub.queue.get(), timeout=30.0)
                        except asyncio.TimeoutError:
                            yield {"event": "ping", "data": ""}
                            continue
                        if not data:  # 频道关停唤醒信号
                            break
                        yield {"event": "message", "data": data}
                finally:
                    if sub in adapter._stream_subs:
                        adapter._stream_subs.remove(sub)

            return EventSourceResponse(event_generator())

        @fastapi_app.get("/health")
        async def health() -> Dict[str, str]:
            return {"status": "ok", "adapter": "http_api"}

        return fastapi_app


    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """通知/主动消息入口：经 SSE 订阅者投递，不消费同步模式的待回复 future。

        同步模式的 pending future 由回复路径（send_text）独占——审批提示等
        通知若解析掉 future，HTTP 调用方会把提示文本当作最终答复，真实回复
        无处投递。无订阅者时通知按 fire-and-forget 处理（审批决策在 WebUI
        面板进行，不依赖本频道的提示可达性），记 WARNING 留痕。
        """
        try:
            chat_id = request.channel.channel_id
            text_parts = [seg.content for seg in request.segments if seg.type.value == "text"]
            full_text = "\n".join(text_parts) if text_parts else ""
            if not full_text:
                return SendResponse(success=False, error="空消息")
            if not self._deliver_to_subscribers(chat_id, {"type": "reply", "content": full_text}):
                log(f"http_api 通知无 SSE 订阅者，已丢弃: chat_id={chat_id}", "WARNING")
            return SendResponse(success=True, message_id=f"http-{int(time.time() * 1000)}")
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id="http_api_bot",
            user_name="HTTP API",
            role=ChannelUserRole.MEMBER,
            is_bot=True,
        )

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        return ChannelInfo(
            channel_id=channel_id,
            channel_name="HTTP API Session",
            channel_type=ChannelType.PRIVATE,
        )

    async def health_check(self) -> HealthStatus:
        """健康探针：检查 uvicorn server 状态。"""
        if self._server is None:
            return HealthStatus(
                healthy=False,
                detail="uvicorn server not started",
                last_error="not_started",
            )
        if self._server_task and self._server_task.done():
            exc = self._server_task.exception() if not self._server_task.cancelled() else None
            return HealthStatus(
                healthy=False,
                detail=f"uvicorn server task done: {exc}",
                last_error=str(exc) if exc else "task_done",
            )
        return HealthStatus(
            healthy=True,
            detail=f"HTTP API listening on {self.config.host}:{self.config.port}",
            last_success_at=time.time(),
        )
