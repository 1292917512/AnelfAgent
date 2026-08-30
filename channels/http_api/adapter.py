"""HTTP API 频道 — 内嵌 uvicorn 的通用 HTTP 通信接口。

继承 BaseChannel，声明 SEND_TEXT 能力。
启动后自动监听 HTTP 端口，外部系统通过 POST /api/chat 发送消息并同步获取回复。
"""

from __future__ import annotations

import asyncio
import hmac
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
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

from .config import HTTP_API_CONFIGS

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


class ChatResponse(BaseModel):
    request_id: str
    status: str = "ok"
    reply: str = ""
    error: str = ""


# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------



class HttpApiConfig(ChannelConfig):
    """HTTP 接口 频道配置。"""

    host: str = Field(default="127.0.0.1", description="监听地址")
    port: int = Field(default=8091, description="监听端口")
    reply_timeout: int = Field(default=60, description="回复超时时间（秒）")
    api_token: str = Field(default="", description="API Token（空则免认证）")


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
    _adapter_configs = HTTP_API_CONFIGS

    def __init__(self) -> None:
        self._pending_replies: Dict[str, asyncio.Future[str]] = {}
        self._server: Optional[Any] = None
        self._server_task: Optional[asyncio.Task[None]] = None
        super().__init__()

    channel_id = "http_api"

    display_name = "HTTP 接口"

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
        for fut in self._pending_replies.values():
            if not fut.done():
                fut.cancel()
        self._pending_replies.clear()
        self._status = ChannelStatus.STOPPED
        log("HTTP API 频道已停止")

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """回复 HTTP API 调用方。"""
        fut = self._pending_replies.pop(chat_id, None)
        if fut and not fut.done():
            fut.set_result(text)
            return _ok({"chat_id": chat_id})
        return _err(f"无待回复请求: chat_id={chat_id}")

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
            fut = adapter._expect_reply(reply_key)
            session_id = req.session_id or (req.group_id if req.group_id else req.user_id)
            message_id = req.message_id or req.request_id

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

        @fastapi_app.get("/health")
        async def health() -> Dict[str, str]:
            return {"status": "ok", "adapter": "http_api"}

        return fastapi_app


    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口：通过 pending future 返回响应。"""
        try:
            chat_id = request.channel.channel_id
            fut = self._pending_replies.pop(chat_id, None)
            if not fut or fut.done():
                return SendResponse(success=False, error=f"无待回复请求: chat_id={chat_id}")

            # 拼接所有 text segment
            text_parts = [seg.content for seg in request.segments if seg.type.value == "text"]
            full_text = "\n".join(text_parts) if text_parts else ""
            fut.set_result(full_text)
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
