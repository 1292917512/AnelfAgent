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
    token 影响 —— 仅 http_api 会话每轮约三百 token，契约详文按需加载；
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
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus, _err, _ok
from agent.channel.dedup import MessageDedup
from agent.channel.reply_policy import ReplyPolicy
from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
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
    notification_channel,
    notification_sender,
)
from agent.channel.tool_bridge import channel_tool
from agent.messages import build_entity_scope
from core.log import log

from .config import HttpApiConfig

# ------------------------------------------------------------------
# Request / Response
# ------------------------------------------------------------------

class ImageItem(BaseModel):
    url: str = Field(default="", max_length=2048)
    base64: str = ""
    mime_type: str = Field(default="image/jpeg", max_length=100)


class MediaItem(BaseModel):
    """非图片媒体入站项（音频/视频/文件；二进制经 base64 上传落盘）。"""

    kind: str = Field(default="file", max_length=16)  # audio / video / file
    base64: str = ""
    mime_type: str = Field(default="", max_length=100)
    file_name: str = Field(default="", max_length=255)


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=256_000)
    user_id: str = Field(default="api_user", max_length=128)
    user_name: str = Field(default="", max_length=128)
    group_id: str = Field(default="", max_length=128)
    session_id: str = Field(default="", max_length=128)
    message_id: str = Field(default="", max_length=128)
    reply_to_id: str = Field(default="", max_length=128)
    reply_content: str = Field(default="", max_length=2000)
    to_me: bool = True
    # 消息类别：chat（默认）/ notification / event / system，对应历史 [kind:xxx] 标签
    kind: str = "chat"
    images: List[ImageItem] = Field(default_factory=list)
    media: List[MediaItem] = Field(default_factory=list)
    # 会话级出站回调：携带即注册，后续回复与主动消息推送到该 webhook
    callback_url: str = Field(default="", max_length=2048)
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12], max_length=128)
    # 立即返回不等待回复（回复经 /api/chat/stream 或 callback_url 投递）
    async_mode: bool = False


class ChatResponse(BaseModel):
    request_id: str
    status: str = "ok"
    reply: str = ""
    error: str = ""


class VisionPushRequest(BaseModel):
    """外部视觉源推送一帧（缓冲中记为 external:<source>）。"""

    source: str = Field(min_length=1, max_length=64)
    image_base64: str
    mime_type: str = Field(default="image/png", max_length=100)
    captured_at: Optional[float] = None


class _StreamSub:
    """单个 SSE 订阅者（scope 前缀过滤 + 帧队列）。"""

    __slots__ = ("prefix", "queue")

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.queue: asyncio.Queue[str] = asyncio.Queue()


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
        version="1.1.0",
        author="AnelfAgent",
    )
    _Configs = HttpApiConfig

    def __init__(self) -> None:
        self._pending_replies: Dict[tuple[str, str], asyncio.Future[str]] = {}
        self._server: Optional[Any] = None
        self._server_task: Optional[asyncio.Task[None]] = None
        self._stream_subs: List[_StreamSub] = []
        # (chat_id, session_id) → 会话级出站回调
        self._callbacks: Dict[tuple[str, str], str] = {}
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
        return http_api_policy(self.config)

    # ------------------------------------------------------------------
    # AI 自助：门户规范 help 工具
    # ------------------------------------------------------------------

    def portal_spec(self) -> Dict[str, Any]:
        """门户接入契约（按生效配置实时生成；永不含令牌值）。"""
        cfg = self.config
        base = f"http://{cfg.host}:{cfg.port}"
        return {
            "channel": self.channel_id,
            "purpose": "外部系统与 Agent 的双向 HTTP 门户：文本/图片/音频/视频/文件/视觉画面全部可经 HTTP 传入",
            "base_url": base,
            "auth": {
                "required": bool(cfg.api_token),
                "how": "请求头 x-api-token 或 Authorization: Bearer；SSE/WS 可用 ?token=" if cfg.api_token
                       else "未配置令牌（仅回环监听可用）",
            },
            "inbound": {
                "chat": {
                    "method": "POST", "path": f"{base}/api/chat",
                    "fields": {
                        "message": "消息文本（≤256K 字符，与附件至少其一）",
                        "user_id / group_id / session_id": "身份与会话归属（历史/记忆按它隔离延续）",
                        "kind": "chat（默认）/ event / system → 历史 [kind:xxx] 标签；"
                                "notification → 聚合进通知中心会话（强制异步受理，无同步回复）",
                        "images": "图片 [{url|base64, mime_type}]，落盘后进视觉通道",
                        "media": "非图片媒体 [{kind: audio|video|file, base64, mime_type, file_name}]，≤50MB 落盘为媒体段",
                        "reply_to_id / reply_content": "引用回复（可选）",
                        "async_mode": "false 同步阻塞等回复（≤reply_timeout 秒）；true 立即返回 accepted",
                        "callback_url": "携带即注册本会话出站回调（http/https）",
                        "message_id": "幂等键：重复 id 返回 duplicate 不重复入管道",
                    },
                    "status": "ok / accepted / timeout / duplicate / error",
                },
                "vision_push": {
                    "method": "POST", "path": f"{base}/api/vision/push",
                    "fields": {"source": "源名", "image_base64": "png/jpeg/webp ≤20MB", "captured_at": "可选时间戳"},
                    "note": "单帧推送，缓冲记 external:<source>，与本地捕获同判变同注入",
                },
                "vision_stream": {
                    "method": "WS", "path": f"{base}/api/vision/stream",
                    "fields": "每帧一条 JSON 文本（同 vision_push 字段）",
                    "note": "持续推流入口：实时画面帧逐帧上行，系统内部完成判变/缓冲/注入；每帧回 ack",
                },
                "sse": {
                    "method": "GET", "path": f"{base}/api/chat/stream?user_id=<id>",
                    "note": "订阅该身份全部会话的 delta/tool_call/reply/turn_end 帧",
                },
                "health": {"method": "GET", "path": f"{base}/health"},
            },
            "outbound": {
                "cascade": ["同步响应（pending 请求）", "SSE 订阅 reply 帧", "webhook 回调 POST"],
                "callback_url": cfg.callback_url or "未配置默认回调（请求可带 callback_url 按会话注册）",
                "payload": "{event, chat_id, message_id, segments, ts, session_id?}；媒体段 ≤20MB 内联 base64，否则仅 path",
                "delivery": "含媒体消息整体经回调；无订阅者或可用回调时返回投递失败",
            },
            "limits": {
                "reply_timeout_s": cfg.reply_timeout,
                "media_max_mb": 50, "vision_frame_max_mb": 20, "callback_inline_max_mb": 20,
                "attachments_max": 9, "message_max_chars": 256_000, "body_max_mb": 80,
            },
        }

    @channel_tool(description="获取 HTTP API 门户的完整接入规范（端点/字段/认证/收发级联），外部系统对接前先读")
    def portal_help(self) -> str:
        """返回本频道的双向接入契约（按生效配置实时生成）。"""
        return json.dumps(self.portal_spec(), ensure_ascii=False, indent=2)

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
        # ws_max_size 放开到 32MB：20MB 图片帧 base64 后约 27MB
        config = uvicorn.Config(
            app, host=host, port=port, log_level="warning",
            ws_max_size=32 * 1024 * 1024,
        )
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())
        self._subscribe_stream_events()
        self._status = ChannelStatus.RUNNING
        outbound = f"，出站回调: {self.config.callback_url}" if self.config.callback_url else ""
        log(f"HTTP API 频道已启动: http://{host}:{port}（认证: {'token' if self.config.api_token else '仅回环'}）{outbound}")

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
        """回复 HTTP API 调用方（pending future → SSE 订阅者 → webhook 回调级联）。"""
        session_id = str(kwargs.get("session_id") or "")
        fut = self._pending_replies.pop(self._delivery_key(chat_id, session_id), None)
        if fut and not fut.done():
            fut.set_result(text)
            return _ok({"chat_id": chat_id})
        # async 模式：无 pending future 时经 SSE reply 帧投递最终文本
        payload: Dict[str, Any] = {"type": "reply", "content": text}
        if session_id and session_id != chat_id:
            payload["session_id"] = session_id
        if self._deliver_to_subscribers(chat_id, payload):
            return _ok({"chat_id": chat_id, "delivery": "stream"})
        segments = [SendSegment(type=SegmentType.TEXT, content=text)]
        try:
            if await self._deliver_callback(chat_id, segments, session_id=session_id):
                return _ok({"chat_id": chat_id, "delivery": "callback"})
        except Exception as exc:
            return _err(f"回调投递失败: chat_id={chat_id}: {exc}")
        return _err(f"无待回复请求: chat_id={chat_id}")

    def _deliver_to_subscribers(self, chat_id: str, payload: Dict[str, Any]) -> bool:
        """向匹配该 chat 的 SSE 订阅者推送一帧，返回是否有订阅者送达。"""
        candidates = (
            build_entity_scope("user", self.channel_id, chat_id),
            build_entity_scope("group", self.channel_id, chat_id),
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
    # 出站 webhook 回调（AI → 外部系统）
    # ------------------------------------------------------------------

    @staticmethod
    def _delivery_key(chat_id: str, session_id: str = "") -> tuple[str, str]:
        """统一归一化 HTTP 身份与子会话投递键。"""
        return chat_id, session_id or chat_id

    def _resolve_callback(self, chat_id: str, session_id: str = "") -> str:
        """会话级回调优先，频道默认回调兜底。"""
        return (
            self._callbacks.get(self._delivery_key(chat_id, session_id))
            or self._callbacks.get(self._delivery_key(chat_id))
            or self.config.callback_url
        )

    def _register_callback(self, chat_id: str, url: str, session_id: str = "") -> None:
        """登记会话级回调；表满时淘汰最旧条目（dict 保插入序）。"""
        key = self._delivery_key(chat_id, session_id)
        self._callbacks.pop(key, None)
        while len(self._callbacks) >= _MAX_CALLBACKS:
            self._callbacks.pop(next(iter(self._callbacks)))
        self._callbacks[key] = url

    async def _deliver_callback(
        self, chat_id: str, segments: List[SendSegment], *,
        message_id: str | None = None, session_id: str = "",
    ) -> bool:
        """把消息段 POST 到 webhook 回调；未配置回调返回 False，投递失败抛错由调用方归因。"""
        url = self._resolve_callback(chat_id, session_id)
        if not url:
            return False
        payload: Dict[str, Any] = {
            "event": "message",
            "chat_id": chat_id,
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
            return True
        provided = request.query_params.get("token", "")
        if not provided:
            provided = request.headers.get("x-api-token", "")
            auth = request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()
        return bool(provided) and hmac.compare_digest(provided, token)

    def _check_ws_auth(self, websocket: WebSocket) -> bool:
        """WS 鉴权：token 走 ?token= 查询参数或请求头（与主 WS 同一约定）。"""
        token = self.config.api_token
        if not token:
            return True
        provided = websocket.query_params.get("token", "")
        if not provided:
            provided = websocket.headers.get("x-api-token", "")
            auth = websocket.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                provided = auth[7:].strip()
        return bool(provided) and hmac.compare_digest(provided, token)

    def _expect_reply(self, chat_id: str, session_id: str = "") -> asyncio.Future[str]:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        reply_key = self._delivery_key(chat_id, session_id)
        # 同 key 已有挂起请求（同用户并发调用）：立即失败旧 future——
        # 否则旧请求干等超时，且 Agent 回复会错配给新请求
        old = self._pending_replies.get(reply_key)
        if old is not None and not old.done():
            old.set_exception(RuntimeError("该会话已有更新的请求，本请求被取代"))
        self._pending_replies[reply_key] = fut
        return fut

    def _create_app(self) -> FastAPI:
        timeout: int = int(self.config.reply_timeout)

        fastapi_app = FastAPI(title="AnelfAgent HTTP API", version="1.0.0")
        adapter = self

        @fastapi_app.middleware("http")
        async def _body_limit(request: Request, call_next: Callable[..., Any]) -> Any:
            """请求体硬上限：按 Content-Length 前置拒绝（chunked 由字段级大小门兜底）。"""
            from fastapi.responses import JSONResponse
            length = request.headers.get("content-length", "")
            if length.isdigit() and int(length) > _MAX_BODY_BYTES:
                return JSONResponse(
                    {"status": "error",
                     "error": f"请求体超过 {_MAX_BODY_BYTES // (1024 * 1024)}MB 上限"},
                    status_code=413,
                )
            return await call_next(request)

        @fastapi_app.post("/api/chat", response_model=ChatResponse)
        async def chat(req: ChatRequest, request: Request) -> ChatResponse:
            if not adapter._check_auth(request):
                return ChatResponse(
                    request_id=req.request_id,
                    status="error",
                    error="未认证：缺少或错误的 API Token",
                )
            error = adapter._validate_chat_request(req)
            if error:
                return ChatResponse(request_id=req.request_id, status="error", error=error)
            reply_key = req.group_id if req.group_id else req.user_id
            delivery_key = adapter._delivery_key(reply_key, req.session_id)
            message_id = req.message_id or req.request_id
            kind = MessageKind(req.kind)
            is_notification = kind == MessageKind.NOTIFICATION
            # 去重登记在落盘之前：重试风暴不重复入管道；校验失败的请求不消耗 id。
            # 键按会话命名空间隔离，不同调用方的同号 id 互不误伤
            if adapter._dedup.is_duplicate(json.dumps([*delivery_key, message_id])):
                return ChatResponse(
                    request_id=req.request_id, status="duplicate",
                    error="消息已受理（重复 message_id），不重复投递",
                )
            if req.callback_url and not is_notification:
                adapter._register_callback(reply_key, req.callback_url, req.session_id)
            # 通知不产生对调用方的同步回复（通知中心语义），强制异步受理
            fut = None if (req.async_mode or is_notification) else adapter._expect_reply(
                reply_key, req.session_id,
            )
            try:
                segments = await adapter._build_inbound_segments(req)
            except (ValueError, OverflowError) as exc:
                if fut is not None and adapter._pending_replies.get(delivery_key) is fut:
                    adapter._pending_replies.pop(delivery_key, None)
                if fut is not None and not fut.done():
                    fut.cancel()
                return ChatResponse(request_id=req.request_id, status="error", error=str(exc))

            # 标准入站信封：与 QQ/TG 等频道同一路径（dispatch_inbound 归一化
            # 标签前缀、图片/媒体提取、回复路由登记与群聊判定）；
            # 通知类消息聚合进通知中心会话，真实来源在正文标注
            if is_notification:
                sender = notification_sender(adapter.channel_id, "HTTP API")
                target = notification_channel("HTTP API")
                origin = req.user_name or req.user_id
                content = f"来自 {origin}({req.user_id}) 的通知：\n{req.message}"
            else:
                sender = AdapterUser(
                    platform=adapter.channel_id,
                    user_id=req.user_id,
                    user_name=req.user_name or req.user_id,
                )
                target = AdapterChannel(
                    channel_id=req.group_id if req.group_id else req.user_id,
                    channel_type=ChannelType.GROUP if req.group_id else ChannelType.PRIVATE,
                )
                content = req.message
            inbound = AdapterMessage(
                message_id=message_id,
                sender=sender,
                channel=target,
                content=content,
                segments=segments,
                kind=kind,
                is_to_me=req.to_me,
                reply_to_id=req.reply_to_id,
                reply_content=req.reply_content,
                session_id="" if is_notification else req.session_id,
            )
            from agent.channel.manager import get_channel_manager
            await get_channel_manager().dispatch_inbound(adapter, inbound)

            # async 模式：投递完成即返回，最终回复经 /api/chat/stream 的
            # reply / turn_end 帧或 callback_url 投递（长任务不受 reply_timeout 约束）
            if fut is None:
                return ChatResponse(request_id=req.request_id, status="accepted")

            try:
                reply = await asyncio.wait_for(fut, timeout=timeout)
            except asyncio.TimeoutError:
                if adapter._pending_replies.get(delivery_key) is fut:
                    adapter._pending_replies.pop(delivery_key, None)
                return ChatResponse(
                    request_id=req.request_id,
                    status="timeout",
                    error=f"Agent 回复超时（{timeout}s）",
                )
            except RuntimeError as exc:
                return ChatResponse(request_id=req.request_id, status="error", error=str(exc))

            return ChatResponse(request_id=req.request_id, reply=reply)

        @fastapi_app.get("/api/chat/stream")
        async def chat_stream(user_id: str, request: Request):
            """SSE 流端点：推送该 user 的 delta / tool_call / reply / turn_end 帧。"""
            from fastapi.responses import JSONResponse
            if not adapter._check_auth(request):
                return JSONResponse(
                    {"status": "error", "error": "未认证：缺少或错误的 API Token"},
                    status_code=401,
                )
            from sse_starlette.sse import EventSourceResponse

            sub = _StreamSub(build_entity_scope("user", adapter.channel_id, user_id))
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

        @fastapi_app.post("/api/vision/push")
        async def vision_push(req: VisionPushRequest, request: Request) -> Dict[str, Any]:
            """外部视觉源推送一帧（与主 web 端 /api/vision/push 同一缓冲入口）。"""
            if not adapter._check_auth(request):
                return {"status": "error", "error": "未认证：缺少或错误的 API Token"}
            from agent.vision.framework import push_external_frame
            try:
                result = await push_external_frame(
                    req.source, req.image_base64, req.mime_type, req.captured_at)
            except (ValueError, OverflowError) as exc:
                return {"status": "error", "error": str(exc)}
            return {"status": "ok", **result}

        @fastapi_app.websocket("/api/vision/stream")
        async def vision_stream(websocket: WebSocket) -> None:
            """外部视觉源持续推流：每帧一条 JSON 文本，逐帧汇入统一缓冲并回 ack。

            帧格式同 POST /api/vision/push 的字段；坏帧只回错误帧不断连。
            实时画面的判变、缓冲与上下文注入由系统内部完成，推流方只负责采帧上行。
            """
            if not adapter._check_ws_auth(websocket):
                await websocket.close(code=4401)
                return
            await websocket.accept()
            from agent.vision.framework import push_external_frame
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    raw = message.get("text")
                    if raw is None:
                        # 二进制帧不支持：推流契约是 JSON 文本帧
                        await websocket.send_text(json.dumps(
                            {"status": "error", "error": "仅接受 JSON 文本帧"},
                            ensure_ascii=False))
                        continue
                    try:
                        frame = json.loads(raw)
                        captured_at = frame.get("captured_at")
                        if not isinstance(captured_at, (int, float)):
                            captured_at = None
                        result = await push_external_frame(
                            str(frame.get("source", "")),
                            str(frame.get("image_base64", "")),
                            str(frame.get("mime_type", "image/png")),
                            captured_at,
                        )
                        ack: Dict[str, Any] = {
                            "status": "ok", "changed": result["changed"],
                            "source": result["source"],
                        }
                    except (ValueError, OverflowError) as exc:
                        ack = {"status": "error", "error": str(exc)}
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        ack = {"status": "error", "error": "帧必须是 JSON 文本"}
                    await websocket.send_text(json.dumps(ack, ensure_ascii=False))
            except WebSocketDisconnect:
                pass

        @fastapi_app.get("/health")
        async def health() -> Dict[str, str]:
            return {"status": "ok", "adapter": "http_api"}

        return fastapi_app


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
                    )
                except Exception as exc:
                    return SendResponse(success=False, error=f"回调投递失败: {exc}")
                if not delivered:
                    return SendResponse(success=False, error="媒体消息无可用回调")
            else:
                payload: Dict[str, Any] = {"type": "reply", "content": full_text}
                if session_id and session_id != chat_id:
                    payload["session_id"] = session_id
                if not self._deliver_to_subscribers(chat_id, payload):
                    try:
                        delivered = await self._deliver_callback(
                            chat_id, request.segments, message_id=message_id, session_id=session_id,
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
            detail=f"HTTP API listening on {self.config.host}:{self.config.port}",
            last_success_at=time.time(),
        )

    def get_status_info(self) -> Dict[str, Any]:
        """门户运行态：监听地址、投递面规模与回调配置（不含令牌值）。"""
        info = super().get_status_info()
        if self._server is not None:
            info.update({
                "listen": f"{self.config.host}:{self.config.port}",
                "auth": "token" if self.config.api_token else "loopback",
                "sse_subscribers": len(self._stream_subs),
                "session_callbacks": len(self._callbacks),
                "default_callback": bool(self.config.callback_url),
                "pending_replies": len(self._pending_replies),
            })
        return info
