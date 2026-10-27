"""HTTP API 频道 — 内嵌 uvicorn 的通用 HTTP 双向通信门户。

继承 BaseChannel。入站：POST /api/chat 消息问答（文本/图片/媒体，async_mode 立即返回）、
POST /api/vision/push 外部视觉源单帧推送、WS /api/vision/stream 持续推流、
GET /api/chat/stream 订阅回复流式帧（delta / tool_call / reply / turn_end）。
出站：同步响应 → SSE 订阅 → webhook 回调（ChatRequest.callback_url 按会话注册，
config.callback_url 兜底）三级投递级联；含媒体的完整消息经回调以结构化段投递
（小文件内联 base64），无可用接收端返回失败。
AI 自助：portal_help 工具返回按生效配置实时生成的完整接入契约。

Model Experience:
    模型看到什么 —— 门户语义与收发纪律经 reply_policy 注入（channel_policy 层），
        完整契约经 http_api_portal_help 工具按需自取；
    token 影响 —— 仅 http_api 会话每轮数百 token，契约详文按需加载；
    缓存影响 —— channel_policy 尾部层，不触碰 stable 前缀。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, Request, WebSocket

from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus, _err, _ok
from agent.channel.dedup import MessageDedup
from agent.channel.reply_policy import ReplyPolicy
from agent.channel.schemas import (
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    MessageKind,
    MessageSegment,
    SegmentType,
    SendRequest,
    SendResponse,
    SendSegment,
)
from agent.channel.tool_bridge import channel_tool
from agent.messages import build_entity_scope
from core.log import log

from .config import HttpApiConfig
from .help import HelpTopic
from .schemas import ChatRequest
from .schemas import ImageItem as ImageItem
from .schemas import MediaItem as MediaItem

# ------------------------------------------------------------------
# Request / Response
# ------------------------------------------------------------------

class _StreamSub:
    """有界 SSE 订阅；子会话精确匹配，慢客户端溢出后关闭。"""

    __slots__ = ("prefix", "queue", "include_children", "closed")

    def __init__(self, prefix: str, *, include_children: bool = True, maxsize: int = 256) -> None:
        self.prefix = prefix
        self.include_children = include_children
        self.closed = False
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)

    def matches(self, scope: str) -> bool:
        return scope == self.prefix or (self.include_children and scope.startswith(self.prefix + "#"))

    def push(self, data: str) -> bool:
        if self.closed:
            return False
        if self.queue.full():
            self.close()
            return False
        self.queue.put_nowait(data)
        return True

    def close(self) -> None:
        self.closed = True
        while not self.queue.empty():
            self.queue.get_nowait()
        self.queue.put_nowait("")


# 入站媒体段类型映射（图片经 IMAGE 段走图片通道，不在此列）
_MEDIA_SEG_TYPES = {
    "audio": SegmentType.AUDIO,
    "video": SegmentType.VIDEO,
    "file": SegmentType.FILE,
}
# file_name 缺后缀时的 mime → 扩展名兜底
_MEDIA_MIME_EXTS = {
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/ogg": ".ogg",
    "audio/flac": ".flac", "audio/mp4": ".m4a", "audio/amr": ".amr",
    "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
}
_MEDIA_MAX_BYTES = 50 * 1024 * 1024
# 回调投递时本地文件内联 base64 的上限（超出仅投递路径）
_CALLBACK_INLINE_MAX_BYTES = 20 * 1024 * 1024
# 图片 mime → 扩展名（base64 图片落盘用）
_IMAGE_MIME_EXTS = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/gif": ".gif", "image/bmp": ".bmp",
}
# 单条消息附件数量上限（对齐 webui 聊天附件纪律）
_MAX_ATTACHMENTS = 9
# HTTP 请求体硬上限：单条 50MB 媒体 base64 后约 68MB，留包络余量
_MAX_BODY_BYTES = 80 * 1024 * 1024
# 会话回调注册表上限（超出淘汰最旧，防长期运行无界增长）
_MAX_CALLBACKS = 1000


# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------


class HttpApiChannel(BaseChannel[HttpApiConfig]):
    """HTTP API 频道。"""

    _entity_description = "HTTP 接口通信频道"

    metadata = ChannelMetadata(
        name="HTTP API",
        description="内嵌 uvicorn 的通用 HTTP 双向通信门户",
        version="1.2.0",
        author="AnelfAgent",
    )
    _Configs = HttpApiConfig

    def __init__(self) -> None:
        self._pending_replies: Dict[str, asyncio.Future[str]] = {}
        self._server: Optional[Any] = None
        self._server_task: Optional[asyncio.Task[None]] = None
        self._bound_base_url = ""
        self._requires_token = False
        self._stream_subs: List[_StreamSub] = []
        self._callbacks: Dict[str, str] = {}
        # message_id 去重：客户端重试/网络重发不重复入管道（全频道共用去重器）
        self._dedup = MessageDedup()
        super().__init__()

    channel_id = "http_api"

    display_name = "HTTP 接口"

    display_order = 40

    capabilities: Set[ChannelCapability] = {
        ChannelCapability.SEND_TEXT,
        ChannelCapability.SEND_PHOTO,
        ChannelCapability.SEND_VIDEO,
        ChannelCapability.SEND_AUDIO,
        ChannelCapability.SEND_VOICE,
        ChannelCapability.SEND_FILE,
    }

    @property
    def reply_policy(self) -> ReplyPolicy:
        """HTTP 门户的收发规则（按生效配置生成地址与等待窗口）。"""
        from .reply_policy import http_api_policy
        return http_api_policy(self.portal_config)

    @property
    def portal_config(self) -> HttpApiConfig:
        """帮助保留真实监听地址，避免待重启的配置提前变成外部接入地址。"""
        if self._bound_base_url:
            return self.config.model_copy(update={"public_base_url": self._bound_base_url})
        return self.config

    # ------------------------------------------------------------------
    # AI 自助：门户规范 help 工具
    # ------------------------------------------------------------------

    def portal_spec(self) -> Dict[str, Any]:
        """按生效配置生成机器可读接入契约，不含令牌值。"""
        from .help import build_portal_spec
        return build_portal_spec(self.portal_config)

    @channel_tool(description="HTTP 频道接入帮助：默认 overview；按需 topic=chat/sessions/sdk/https，开发外部应用前先读")
    def portal_help(self, topic: HelpTopic = "overview") -> str:
        """获取接入帮助；默认概览，可选 chat/sessions/sdk/https 主题。"""
        from core.tool_errors import error_from_exception

        from .help import build_portal_help
        try:
            payload = build_portal_help(self.portal_config, topic)
            payload["status"] = self.status.value
            return json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception as exc:
            return error_from_exception(exc, action="获取 HTTP 频道帮助")

    async def start(self) -> None:
        import uvicorn

        if self._server_task is not None and not self._server_task.done():
            return
        settings = self.config
        host: str = settings.host
        port: int = int(settings.port)
        bound_base_url = settings.base_url

        if (not self._is_loopback(host) or settings.public_base_url) and not settings.api_token:
            raise RuntimeError(
                f"HTTP API 频道声明公开接入（监听 {host}）但未配置 api_token，"
                "拒绝启动（外部请求将无法被认证）。请配置 api_token 或将 host 改为 127.0.0.1"
            )

        app = self._create_app()
        # ws_max_size 放开到 32MB：20MB 图片帧 base64 后约 27MB
        config = uvicorn.Config(
            app, host=host, port=port, log_level="warning",
            ws_max_size=32 * 1024 * 1024,
            ssl_certfile=settings.ssl_certfile or None,
            ssl_keyfile=settings.ssl_keyfile or None,
            ssl_keyfile_password=settings.ssl_keyfile_password or None,
            timeout_graceful_shutdown=3,
            limit_concurrency=settings.max_connections,
        )
        config.load()
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._serve())
        try:
            async with asyncio.timeout(10):
                while not self._server.started:
                    if self._server_task.done():
                        await self._server_task
                        raise RuntimeError("HTTP API 服务器未完成启动")
                    await asyncio.sleep(0.01)
        except BaseException:
            await self.stop()
            raise
        self._subscribe_stream_events()
        self._bound_base_url = bound_base_url
        self._status = ChannelStatus.RUNNING
        outbound = f"，出站回调: {self.config.callback_url}" if self.config.callback_url else ""
        log(f"HTTP API 频道已启动: {bound_base_url}（认证: {'token' if self.config.api_token else '仅回环'}）{outbound}")

    async def _serve(self) -> None:
        """内嵌服务器的绑定失败作为频道异常传播，避免 SystemExit 退出整个宿主。"""
        server = self._server
        if server is None:
            raise RuntimeError("HTTP API 服务器尚未创建")
        try:
            await server.serve()
        except SystemExit as exc:
            raise RuntimeError(f"HTTP API 监听失败: {self.config.host}:{self.config.port}") from exc

    async def stop(self) -> None:
        for sub in self._stream_subs:
            sub.close()
        self._stream_subs.clear()
        for fut in self._pending_replies.values():
            if not fut.done():
                fut.set_exception(RuntimeError("HTTP API 频道已停止"))
        self._pending_replies.clear()
        if self._server:
            self._server.should_exit = True
        if self._server_task:
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, KeyboardInterrupt):
                log("stop 异常已忽略", "DEBUG")
            except Exception as exc:
                log(f"HTTP API 服务收尾: {type(exc).__name__}", "DEBUG")
            self._server_task = None
        self._server = None
        self._bound_base_url = ""
        self._requires_token = False
        from core.event_bus import event_bus
        event_bus.off_by_owner("channel:http_api")
        self._callbacks.clear()
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
            if sub.matches(scope):
                sub.push(data)

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
        scope = str(payload.get("scope", ""))
        reason = str(payload.get("reason", "completed"))
        if reason in {"interrupted", "failed"}:
            fut = self._pending_replies.pop(scope, None)
            if fut is not None and not fut.done():
                fut.set_exception(RuntimeError("执行已中止" if reason == "interrupted" else "执行失败"))
        self._push_frame({
            "type": "turn_end",
            "scope": scope,
            "turn_id": str(payload.get("turn_id", "")),
            "reason": reason,
            "error": bool(payload.get("error")),
        }, scope)

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """回复 HTTP API 调用方（pending future → SSE 订阅者 → webhook 回调级联）。"""
        session_id = str(kwargs.get("session_id") or "")
        channel_type = str(kwargs.get("channel_type") or "private")
        fut = self._pending_replies.pop(self._delivery_key(chat_id, session_id, channel_type), None)
        if fut and not fut.done():
            fut.set_result(text)
            return _ok({"chat_id": chat_id})
        # async 模式：无 pending future 时经 SSE reply 帧投递最终文本
        payload: Dict[str, Any] = {"type": "reply", "content": text}
        if session_id and session_id != chat_id:
            payload["session_id"] = session_id
        if self._deliver_to_subscribers(chat_id, payload, session_id=session_id, channel_type=channel_type):
            return _ok({"chat_id": chat_id, "delivery": "stream"})
        segments = [SendSegment(type=SegmentType.TEXT, content=text)]
        try:
            if await self._deliver_callback(chat_id, segments, session_id=session_id, channel_type=channel_type):
                return _ok({"chat_id": chat_id, "delivery": "callback"})
        except Exception as exc:
            return _err(f"回调投递失败: chat_id={chat_id}: {exc}")
        return _err(f"无待回复请求: chat_id={chat_id}")

    def _deliver_to_subscribers(
        self, chat_id: str, payload: Dict[str, Any], *, session_id: str = "", channel_type: str = "private",
    ) -> bool:
        """向匹配该 chat 的 SSE 订阅者推送一帧，返回是否有订阅者送达。"""
        scope = self._delivery_key(chat_id, session_id, channel_type)
        data = json.dumps(payload, ensure_ascii=False)
        delivered = False
        for sub in list(self._stream_subs):
            if sub.matches(scope):
                delivered = sub.push(data) or delivered
        return delivered

    # ------------------------------------------------------------------
    # 出站 webhook 回调（AI → 外部系统）
    # ------------------------------------------------------------------

    @staticmethod
    def _delivery_key(chat_id: str, session_id: str = "", channel_type: str = "private") -> str:
        """统一归一化 HTTP 身份与子会话投递键。"""
        return build_entity_scope("group" if channel_type == "group" else "user", "http_api", chat_id, session_id)

    def _resolve_callback(self, chat_id: str, session_id: str = "", channel_type: str = "private") -> str:
        """会话级回调优先，频道默认回调兜底。"""
        return (
            self._callbacks.get(self._delivery_key(chat_id, session_id, channel_type))
            or self._callbacks.get(self._delivery_key(chat_id, channel_type=channel_type))
            or self.config.callback_url
        )

    def _register_callback(self, chat_id: str, url: str, session_id: str = "", channel_type: str = "private") -> None:
        """登记会话级回调；表满时淘汰最旧条目（dict 保插入序）。"""
        key = self._delivery_key(chat_id, session_id, channel_type)
        self._callbacks.pop(key, None)
        while len(self._callbacks) >= _MAX_CALLBACKS:
            self._callbacks.pop(next(iter(self._callbacks)))
        self._callbacks[key] = url

    async def _deliver_callback(
        self, chat_id: str, segments: List[SendSegment], *,
        message_id: str | None = None, session_id: str = "", channel_type: str = "private",
    ) -> bool:
        """把消息段 POST 到 webhook 回调；未配置回调返回 False，投递失败抛错由调用方归因。"""
        url = self._resolve_callback(chat_id, session_id, channel_type)
        if not url:
            return False
        payload: Dict[str, Any] = {
            "event": "message",
            "chat_id": chat_id,
            "channel_type": channel_type,
            "scope": self._delivery_key(chat_id, session_id, channel_type),
            "message_id": message_id or f"http-{uuid.uuid4().hex}",
            "segments": [await self._serialize_segment(seg) for seg in segments],
            "ts": time.time(),
        }
        if session_id and session_id != chat_id:
            payload["session_id"] = session_id
        headers: Dict[str, str] = {}
        if self.config.callback_token:
            headers["authorization"] = f"Bearer {self.config.callback_token}"
        from core.network import async_network_client
        async with async_network_client(timeout=float(self.config.callback_timeout)) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
        return True

    @staticmethod
    async def _serialize_segment(seg: SendSegment) -> Dict[str, Any]:
        """消息段 → 回调 JSON 段；本地小文件内联 base64 便于外部直接消费。"""
        kind = seg.type.value
        if kind == "text":
            return {"type": "text", "text": seg.content}
        item: Dict[str, Any] = {"type": kind, "caption": seg.caption}
        if seg.mime_type:
            item["mime_type"] = seg.mime_type
        path = seg.file_path
        if path:
            item["path"] = path

            def _read_inline() -> str:
                if not os.path.isfile(path) or os.path.getsize(path) > _CALLBACK_INLINE_MAX_BYTES:
                    return ""
                import base64 as _b64
                with open(path, "rb") as fh:
                    return _b64.b64encode(fh.read()).decode()

            try:
                inline = await asyncio.to_thread(_read_inline)
            except OSError:
                inline = ""
                log(f"回调媒体读取失败，仅投递路径: {path}", "WARNING")
            if inline:
                item["base64"] = inline
        return item

    # ------------------------------------------------------------------
    # 入站媒体落盘（外部系统 → AI）
    # ------------------------------------------------------------------

    async def _save_inbound_file(
        self, data: bytes, kind_dir: str, file_name: str, mime_type: str,
        ext_map: Dict[str, str],
    ) -> str:
        """落盘到 uploads/<kind_dir>/，命名沿用频道媒体约定 {ts}_{短id}_{名}。"""
        from core.path import ConfigPaths
        safe_name = os.path.basename(file_name) if file_name else ""
        ext = os.path.splitext(safe_name)[1].lower() or ext_map.get(mime_type, ".bin")
        out_dir = Path(str(ConfigPaths.UPLOAD_DIR)) / kind_dir
        stamp = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        dest = out_dir / (f"{stamp}_{safe_name}" if safe_name else f"{stamp}{ext}")

        def _write() -> None:
            out_dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        await asyncio.to_thread(_write)
        return str(dest)

    @staticmethod
    def _decode_base64(payload: str, what: str, max_bytes: int) -> bytes:
        """严格 base64 解码 + 大小门。

        Raises:
            ValueError: 缺内容 / base64 非法。OverflowError: 超过上限。
        """
        if not payload:
            raise ValueError(f"{what}缺少 base64 内容")
        import base64 as _b64
        import binascii
        try:
            data = _b64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError(f"{what}不是合法的 base64") from None
        if not data or len(data) > max_bytes:
            raise OverflowError(f"{what}为空或超过 {max_bytes // (1024 * 1024)}MB 上限")
        return data

    async def _build_inbound_segments(self, req: ChatRequest) -> List[MessageSegment]:
        """把请求的 images/media 组装为标准消息段（图片文件化，媒体落盘）。"""
        segments: List[MessageSegment] = []
        for img in req.images:
            if bool(img.url) == bool(img.base64):
                raise ValueError("图片必须提供 url 或 base64，且只能提供其中一个")
            if img.url:
                if not img.url.lower().startswith(("http://", "https://")):
                    raise ValueError(f"图片 url 仅支持 http/https: {img.url}")
                segments.append(MessageSegment(type=SegmentType.IMAGE, url=img.url))
            elif img.base64:
                data = self._decode_base64(img.base64, "图片", _MEDIA_MAX_BYTES)
                path = await self._save_inbound_file(data, "image", "", img.mime_type, _IMAGE_MIME_EXTS)
                segments.append(MessageSegment(
                    type=SegmentType.IMAGE, file_path=path,
                    file_name=os.path.basename(path), mime_type=img.mime_type,
                    file_size=len(data),
                ))
        for item in req.media:
            kind = item.kind.strip().lower()
            seg_type = _MEDIA_SEG_TYPES.get(kind)
            if seg_type is None:
                raise ValueError(f"未知媒体类型: {item.kind}（支持 audio/video/file）")
            data = self._decode_base64(item.base64, f"{kind} 媒体", _MEDIA_MAX_BYTES)
            path = await self._save_inbound_file(data, kind, item.file_name, item.mime_type, _MEDIA_MIME_EXTS)
            segments.append(MessageSegment(
                type=seg_type, file_path=path,
                file_name=os.path.basename(path), mime_type=item.mime_type,
                file_size=len(data),
            ))
        return segments

    def _validate_chat_request(self, req: ChatRequest) -> str:
        """廉价前置校验（在落盘/去重登记之前）；返回空串表示通过。"""
        if not req.message.strip() and not req.images and not req.media:
            return "空消息：message 与附件至少其一"
        if len(req.images) + len(req.media) > _MAX_ATTACHMENTS:
            return f"附件数量超限（上限 {_MAX_ATTACHMENTS}）"
        if req.callback_url and not req.callback_url.lower().startswith(("http://", "https://")):
            return "callback_url 仅支持 http/https"
        try:
            MessageKind(req.kind)
        except ValueError:
            return f"未知消息类别: {req.kind}（支持 chat/notification/event/system）"
        return ""

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _is_loopback(host: str) -> bool:
        return host in ("127.0.0.1", "localhost", "::1")

    def _check_auth(self, request: Request) -> bool:
        """校验 API Token（未配置时仅允许回环来源，启动时已强制）。

        令牌可经请求头（x-api-token / Authorization: Bearer）或 ?token=
        查询参数携带（EventSource 等不能自定义头的客户端）。
        """
        token = self.config.api_token
        if not token:
            return not self._requires_token and self._is_loopback(self.config.host) and not self.config.public_base_url
        provided = request.query_params.get("token", "")
        if not provided:
            provided = request.headers.get("x-api-token", "")
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()
        return bool(provided) and hmac.compare_digest(provided.encode(), token.encode())

    def _check_ws_auth(self, websocket: WebSocket) -> bool:
        """WS 鉴权：token 走 ?token= 查询参数或请求头（与主 WS 同一约定）。"""
        token = self.config.api_token
        if not token:
            return not self._requires_token and self._is_loopback(self.config.host) and not self.config.public_base_url
        provided = websocket.query_params.get("token", "")
        if not provided:
            provided = websocket.headers.get("x-api-token", "")
            auth = websocket.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()
        return bool(provided) and hmac.compare_digest(provided.encode(), token.encode())

    def _expect_reply(self, chat_id: str, session_id: str = "", channel_type: str = "private") -> asyncio.Future[str]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        reply_key = self._delivery_key(chat_id, session_id, channel_type)
        # 同 key 已有挂起请求（同用户并发调用）：立即失败旧 future——
        # 否则旧请求干等超时，且 Agent 回复会错配给新请求
        old = self._pending_replies.get(reply_key)
        if old is not None and not old.done():
            old.set_exception(RuntimeError("该会话已有更新的请求，本请求被取代"))
        self._pending_replies[reply_key] = fut
        return fut

    def _create_app(self) -> FastAPI:
        from urllib.parse import urlsplit

        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse
        from starlette.exceptions import HTTPException as StarletteHTTPException

        from agent.llm.model_api import JudgmentError, LLMNotConfiguredError, ModelCallError
        from core.tool_errors import error_from_exception

        from .routes import build_router
        from .sdk_routes import build_sdk_router
        from .transport import HttpBoundary

        self._requires_token = (self._requires_token or not self._is_loopback(self.config.host)
                                or bool(self.config.public_base_url))
        app = FastAPI(title="AnelfAgent HTTP Portal", version="1.2.0",
                      root_path=urlsplit(self.config.public_base_url).path.rstrip("/"))
        app.add_middleware(HttpBoundary, authenticate=self._check_auth, max_body=_MAX_BODY_BYTES)
        if self.config.cors_origins:
            from fastapi.middleware.cors import CORSMiddleware
            app.add_middleware(
                CORSMiddleware, allow_origins=self.config.cors_origins, allow_methods=["GET", "POST"],
                allow_headers=["Authorization", "X-API-Token", "Content-Type"],
                expose_headers=["X-Request-ID"],
            )

        @app.exception_handler(StarletteHTTPException)
        async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
            return JSONResponse({"status": "error", "error": str(exc.detail)},
                                status_code=exc.status_code, headers=exc.headers)

        @app.exception_handler(RequestValidationError)
        async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
            details = [{key: item[key] for key in ("loc", "msg", "type")} for item in exc.errors()]
            return JSONResponse({"status": "error", "error": "请求参数无效", "details": details}, status_code=422)

        async def sdk_error(request: Request, exc: Exception) -> JSONResponse:
            status_code = (503 if isinstance(exc, LLMNotConfiguredError) else
                           502 if isinstance(exc, ModelCallError) else 422 if isinstance(exc, ValueError) else 500)
            return JSONResponse({"status": "error", **json.loads(error_from_exception(exc))}, status_code=status_code,
                                headers={"X-Request-ID": getattr(request.state, "request_id", "")})

        for error_type in (LLMNotConfiguredError, ModelCallError, JudgmentError, ValueError):
            app.add_exception_handler(error_type, sdk_error)
        app.add_exception_handler(Exception, sdk_error)

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "ok", "adapter": "http_api"}

        app.include_router(build_router(self))
        app.include_router(build_sdk_router(self))
        return app

    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """向 SSE 订阅者或回调投递完整消息，仅在确认送达时返回成功。

        含媒体的请求整体走回调；同步模式的 pending future 由 send_text 独占，
        避免通知占用最终答复的位置。
        """
        try:
            chat_id = request.channel.channel_id
            channel_type = request.channel.channel_type.value
            session_id = str(request.extra.get("session_id") or "")
            media_types = {
                SegmentType.IMAGE, SegmentType.VIDEO, SegmentType.AUDIO,
                SegmentType.VOICE, SegmentType.FILE,
            }
            for seg in request.segments:
                if seg.type == SegmentType.TEXT:
                    continue
                if seg.type not in media_types:
                    return SendResponse(success=False, error=f"段类型 {seg.type.value} 不受支持")
                if not seg.file_path.strip():
                    return SendResponse(success=False, error=f"{seg.type.value} 段缺少文件路径")
            text_parts = [seg.content for seg in request.segments if seg.type == SegmentType.TEXT]
            full_text = "\n".join(text_parts) if text_parts else ""
            has_media = any(seg.type != SegmentType.TEXT for seg in request.segments)
            if not full_text and not has_media:
                return SendResponse(success=False, error="空消息")
            message_id = f"http-{uuid.uuid4().hex}"
            if has_media:
                # SSE 只支持文本帧，混合消息须作为整体经回调投递。
                try:
                    delivered = await self._deliver_callback(
                        chat_id, request.segments, message_id=message_id, session_id=session_id,
                        channel_type=channel_type,
                    )
                except Exception as exc:
                    return SendResponse(success=False, error=f"回调投递失败: {exc}")
                if not delivered:
                    return SendResponse(success=False, error="媒体消息无可用回调")
            else:
                payload: Dict[str, Any] = {"type": "reply", "content": full_text}
                if session_id and session_id != chat_id:
                    payload["session_id"] = session_id
                if not self._deliver_to_subscribers(chat_id, payload, session_id=session_id, channel_type=channel_type):
                    try:
                        delivered = await self._deliver_callback(
                            chat_id, request.segments, message_id=message_id, session_id=session_id,
                            channel_type=channel_type,
                        )
                    except Exception as exc:
                        return SendResponse(success=False, error=f"回调投递失败: {exc}")
                    if not delivered:
                        return SendResponse(success=False, error="无 SSE 订阅者或可用回调")
            return SendResponse(success=True, message_id=message_id)
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
            detail=f"HTTP API listening on {self._bound_base_url or self.config.base_url}",
            last_success_at=time.time(),
        )

    def get_status_info(self) -> Dict[str, Any]:
        """门户运行态：监听地址、投递面规模与回调配置（不含令牌值）。"""
        info = super().get_status_info()
        if self._server is not None:
            info.update({
                "listen": self._bound_base_url or self.config.base_url,
                "auth": "token" if self.config.api_token else "loopback",
                "sse_subscribers": len(self._stream_subs),
                "session_callbacks": len(self._callbacks),
                "default_callback": bool(self.config.callback_url),
                "pending_replies": len(self._pending_replies),
            })
        return info
