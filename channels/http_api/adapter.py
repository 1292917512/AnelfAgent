"""HTTP API 频道 — 内嵌 uvicorn 的通用 HTTP 通信接口。

继承 BaseChannel，声明 SEND_TEXT 能力。
启动后自动监听 HTTP 端口，外部系统通过 POST /api/chat 发送消息并同步获取回复。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from agent.channel.channel import BaseChannel, ChannelCapability, ChannelStatus, _ok, _err
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

class HttpApiChannel(BaseChannel):
    """HTTP API 频道。"""

    _entity_description = "HTTP 接口通信频道"
    _adapter_configs = HTTP_API_CONFIGS

    def __init__(self) -> None:
        self._pending_replies: Dict[str, asyncio.Future[str]] = {}
        self._server: Optional[Any] = None
        self._server_task: Optional[asyncio.Task[None]] = None
        super().__init__()

    @property
    def channel_id(self) -> str:
        return "http_api"

    @property
    def display_name(self) -> str:
        return "HTTP 接口"

    @property
    def capabilities(self) -> Set[ChannelCapability]:
        return {ChannelCapability.SEND_TEXT}

    async def start(self) -> None:
        import uvicorn

        host: str = self.get_adapter_config("host", "127.0.0.1")
        port: int = int(self.get_adapter_config("port", 8091))

        app = self._create_app()
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())
        self._status = ChannelStatus.RUNNING
        log(f"HTTP API 频道已启动: http://{host}:{port}")

    async def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._server_task:
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, KeyboardInterrupt):
                pass
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

    def _expect_reply(self, reply_key: str) -> asyncio.Future[str]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_replies[reply_key] = fut
        return fut

    def _create_app(self) -> FastAPI:
        from agent.runtime.agent_app import get_agent_app

        timeout: int = int(self.get_adapter_config("reply_timeout", 60))

        fastapi_app = FastAPI(title="AnelfAgent HTTP API", version="1.0.0")
        fastapi_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        adapter = self

        @fastapi_app.post("/api/chat", response_model=ChatResponse)
        async def chat(req: ChatRequest) -> ChatResponse:
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
