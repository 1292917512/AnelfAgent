"""QQ 回调验证与事件签名，业务回调仅在入站队列受理后确认。"""

import json
import re
import time
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from agent.channel.manager import get_channel_manager
from core.http_endpoints import self_authenticated

from .config import GATEWAY_INTENTS

_MAX_BODY = 1024 * 1024
_CALLBACK_ROUTE = "/webhook"
CALLBACK_PATH = "/api/channels/qqbot" + _CALLBACK_ROUTE


class Webhook:
    def __init__(self, app_id: str, secret: str) -> None:
        self.app_id = app_id
        seed = secret.encode()
        if not seed:
            raise ValueError("缺少 QQ AppSecret")
        self._key = Ed25519PrivateKey.from_private_bytes((seed * 32)[:32])

    async def receive(self, request: Request) -> tuple[dict[str, Any], bool]:
        if request.headers.get("X-Bot-Appid") != self.app_id:
            raise HTTPException(403, "无效的机器人身份")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > _MAX_BODY:
                raise HTTPException(413, "QQ 回调正文过大")
            body.extend(chunk)
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError) as exc:
            raise HTTPException(400, "无效的 QQ 回调 JSON") from exc
        if not isinstance(payload, dict):
            raise HTTPException(400, "无效的 QQ 回调")
        if payload.get("op") == 13:
            data = payload.get("d")
            if not isinstance(data, dict):
                raise HTTPException(400, "缺少 QQ 验证参数")
            plain, ts = data.get("plain_token"), data.get("event_ts")
            # 挑战签名与事件签名共用密钥，不能允许把可执行的事件正文当作挑战签名。
            if (not isinstance(plain, str) or not re.fullmatch(r"[\w.+=/-]{1,256}", plain, re.ASCII)
                    or not isinstance(ts, str) or not re.fullmatch(r"[0-9]{1,12}", ts)):
                raise HTTPException(400, "无效的 QQ 验证参数")
            signature = self._key.sign((ts + plain).encode()).hex()
            return {"plain_token": plain, "signature": signature}, True

        ts = request.headers.get("X-Signature-Timestamp", "")
        signature_hex = request.headers.get("X-Signature-Ed25519", "")
        if not re.fullmatch(r"[0-9]{1,12}", ts) or abs(time.time() - int(ts)) > 300:
            raise HTTPException(403, "无效或过期的 QQ 签名时间")
        try:
            self._key.public_key().verify(bytes.fromhex(signature_hex), ts.encode() + body)
        except (InvalidSignature, ValueError) as exc:
            raise HTTPException(403, "QQ 签名验证失败") from exc
        if payload.get("op") != 0:
            raise HTTPException(400, "不支持的 QQ 回调操作")
        return payload, False


def build_router() -> APIRouter:
    from .adapter import QQBotChannel

    router = APIRouter()

    @router.get("/setup")
    async def setup() -> dict[str, Any]:
        """受 Web 鉴权保护的接入指引和连接事实，停用频道时也可配置。"""
        channel = get_channel_manager().get("qqbot")
        status = channel.get_status_info() if isinstance(channel, QQBotChannel) else {}
        return {"callback_path": CALLBACK_PATH, "intents": GATEWAY_INTENTS,
                "status": status.get("status", "stopped"), "mode": status.get("active_mode"),
                "bot_name": status.get("bot_name", ""), "self_id": status.get("self_id", ""),
                "last_event_at": status.get("last_event_at", 0), "last_error": status.get("last_error", "")}

    @router.post(_CALLBACK_ROUTE)
    @self_authenticated
    async def webhook(request: Request) -> JSONResponse:
        channel = get_channel_manager().get("qqbot")
        if not isinstance(channel, QQBotChannel) or channel.webhook is None:
            raise HTTPException(503, "QQ 官方 Webhook 未运行")
        receiver = channel.webhook
        payload, challenge = await receiver.receive(request)
        if channel.webhook is not receiver:
            raise HTTPException(503, "QQ 频道正在重启")
        if challenge:
            return JSONResponse(payload)
        await channel.dispatch(payload, receiver=receiver)
        return JSONResponse({"op": 12, "d": 0})

    return router
