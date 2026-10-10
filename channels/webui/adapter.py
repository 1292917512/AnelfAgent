"""WebUI 频道 — 接收 Agent 回复并通过 SSE 推送给前端（支持多模态）。"""

from __future__ import annotations

import json
import time
from typing import Any, Set

from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import (
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    SendRequest,
    SendResponse,
)
from core.tags import strip_functional_tags, strip_message_meta_tags

from .config import WebUIConfig


def _clean_outbound(text: str) -> str:
    """出站文本清洗：剥离元数据标签与功能性标签（对齐历史清洗语义）。"""
    return strip_functional_tags(strip_message_meta_tags(text or "")).strip()


def _media_frame_url(path: str) -> str:
    """media 帧 URL 契约：帧内只传可服务 URL，不传本地绝对路径。

    - http(s) / /api/ 开头的引用原样透传；
    - 上传目录（workspace/uploads/{type}/）下的本地路径改写为可服务 URL
      （URL 规则单点定义在 core.path，桌面壳与浏览器拿到同一种东西）；
    - 其余路径原样透传（调用方自担可访问性，记 DEBUG 便于排查）。
    """
    from core.log import log
    from core.path import upload_path_to_url

    if not path or path.startswith(("http://", "https://", "/api/")):
        return path
    url = upload_path_to_url(path)
    if url is not None:
        return url
    log(f"media 帧路径不在上传目录内，原样透传: {path[:80]}", "DEBUG", tag="WebUI")
    return path


class WebUIChannel(BaseChannel[WebUIConfig]):
    """WebUI 频道 — 通过 SSE 向前端推送 Agent 消息（文本/图片/语音/视频）。"""

    _entity_description = "网页界面多媒体频道"

    metadata = ChannelMetadata(
        name="WebUI",
        description="Web 前端 SSE 推送频道",
        version="2.0.0",
        author="AnelfAgent",
    )
    _Configs = WebUIConfig

    channel_id = "webui"

    display_name = "网页界面"

    display_order = 10

    capabilities: Set[ChannelCapability] = {
        ChannelCapability.REALTIME_VOICE,
            ChannelCapability.SEND_TEXT,
            ChannelCapability.SEND_PHOTO,
            ChannelCapability.SEND_VOICE,
            ChannelCapability.SEND_AUDIO,
            ChannelCapability.SEND_VIDEO,
            ChannelCapability.SEND_FILE,
        }

    async def start(self) -> None:
        self._status = ChannelStatus.RUNNING
        self._subscribe_stream_events()

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED
        from core.event_bus import event_bus
        event_bus.off_by_owner("channel:webui")

    # ------------------------------------------------------------------
    # 流式过程事件订阅（内核事件 → SSE 帧；过程性内容，不落对话历史）
    # ------------------------------------------------------------------

    def _subscribe_stream_events(self) -> None:
        from core.event_bus import (
            EVENT_AFTER_REPLY,
            event_bus,
        )
        from core.stream_events import EVENT_CONTEXT_USAGE
        event_bus.on(EVENT_AFTER_REPLY, self._on_after_reply, owner="channel:webui")
        event_bus.on(EVENT_CONTEXT_USAGE, self._on_context_usage, owner="channel:webui")
        # Web 会话的计划与子代理事件供任务面板使用。
        # 事件名与 SSE 帧名一致，表驱动注册（handler 统一为 _broadcast_scoped 转发）。
        from core.event_bus import (
            EVENT_DELEGATION_PROGRESS,
            EVENT_DELEGATION_RESOLVED,
            EVENT_DELEGATION_STARTED,
            EVENT_PLAN_CANCELLED,
            EVENT_PLAN_DELETED,
            EVENT_PLAN_STATUS_CHANGED,
            EVENT_PLAN_STEP_UPDATED,
            EVENT_PLAN_SUBMITTED,
        )
        for evt in (
            EVENT_PLAN_SUBMITTED, EVENT_PLAN_STEP_UPDATED,
            EVENT_PLAN_STATUS_CHANGED, EVENT_PLAN_CANCELLED, EVENT_PLAN_DELETED,
            EVENT_DELEGATION_STARTED, EVENT_DELEGATION_PROGRESS, EVENT_DELEGATION_RESOLVED,
        ):
            event_bus.on(evt, self._make_scoped_forwarder(evt), owner="channel:webui")

    def _make_scoped_forwarder(self, event: str):
        """生成把内核事件透传到 SSE 的 handler（帧名与事件名一致）。"""
        async def _forward(payload: dict) -> None:
            await self._broadcast_scoped(event, payload)
        return _forward

    async def _on_after_reply(self, payload: dict) -> None:
        """轮次结束 → turn_end 帧（前端清除发送态/流式气泡）。

        覆盖无 reply 帧的结束路径（[SILENT] 沉默、空输出、异常），
        避免前端 sending 状态空等看门狗超时。
        """
        await self._broadcast_scoped("turn_end", {"scope": payload.get("scope", ""), "error": bool(payload.get("error"))})

    async def _on_context_usage(self, payload: dict) -> None:
        await self._broadcast_scoped("context_usage", {
            "scope": payload.get("scope", ""),
            "tokens": payload.get("tokens", 0),
            "threshold": payload.get("threshold", 0),
            "window": payload.get("window", 0),
            "percent": payload.get("percent", 0),
            "cache_read_input_tokens": payload.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": payload.get("cache_creation_input_tokens", 0),
            "cache_hit_rate": payload.get("cache_hit_rate", 0.0),
        })

    @staticmethod
    def _resolve_chat_id(target: str, kwargs: dict) -> str:
        """从 session_id kwarg 或 target 的 # 后缀解析 webui 会话窗口 chat_id。"""
        session = str(kwargs.get("session_id") or "")
        if session:
            return session
        target = str(target or "")
        if "#" in target:
            return target.split("#", 1)[1]
        return ""

    @staticmethod
    def _has_online_clients() -> bool:
        """是否有在线 WebUI 客户端（实时枢纽的 web 订阅者，注册中心在 core 层）。"""
        from core import realtime_hub
        return realtime_hub.subscriber_count(client_kind="web") > 0

    def _offline_error(self) -> str | None:
        """无在线客户端时返回失败 JSON。

        无订阅者时广播等于丢消息：如实返回失败，调用方
        会记录投递失败而非把回复标记为已送达。文本与媒体同一语义。
        """
        if not self._has_online_clients():
            return json.dumps(
                {"success": False, "error": "无在线 WebUI 客户端（SSE 未连接）"},
                ensure_ascii=False,
            )
        return None

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        text = _clean_outbound(text)
        if not text:
            return json.dumps({"success": True}, ensure_ascii=False)
        offline = self._offline_error()
        if offline is not None:
            return offline
        await self._broadcast("reply", {
            "content": text,
            "media_type": "text",
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs: Any) -> str:
        offline = self._offline_error()
        if offline is not None:
            return offline
        await self._broadcast("media", {
            "media_type": "image",
            "url": _media_frame_url(photo),
            "caption": _clean_outbound(caption),
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_voice(self, chat_id: str, voice: str, caption: str = "", **kwargs: Any) -> str:
        offline = self._offline_error()
        if offline is not None:
            return offline
        await self._broadcast("media", {
            "media_type": "voice",
            "url": _media_frame_url(voice),
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_audio(self, chat_id: str, audio: str, caption: str = "", **kwargs: Any) -> str:
        offline = self._offline_error()
        if offline is not None:
            return offline
        await self._broadcast("media", {
            "media_type": "audio",
            "url": _media_frame_url(audio),
            "caption": _clean_outbound(caption),
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_video(self, chat_id: str, video: str, caption: str = "", **kwargs: Any) -> str:
        offline = self._offline_error()
        if offline is not None:
            return offline
        await self._broadcast("media", {
            "media_type": "video",
            "url": _media_frame_url(video),
            "caption": _clean_outbound(caption),
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_file(self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any) -> str:
        offline = self._offline_error()
        if offline is not None:
            return offline
        await self._broadcast("media", {
            "media_type": "file",
            "url": _media_frame_url(file_path),
            "caption": _clean_outbound(caption),
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------


    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口（段分发模板见 BaseChannel._forward_via_segment_map，
        各 send_* 方法内部即 SSE 广播）。"""
        return await self._forward_via_segment_map(request)

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id="webui_bot",
            user_name="WebUI Bot",
            role=ChannelUserRole.MEMBER,
            is_bot=True,
        )

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        return ChannelInfo(
            channel_id=channel_id,
            channel_name="WebUI Session",
            channel_type=ChannelType.PRIVATE,
        )

    async def health_check(self) -> HealthStatus:
        """WebUI 健康探针：检查聊天广播事件已有订阅者（web 层 SSE 桥接在线）。"""
        from core.event_bus import EVENT_CHAT_BROADCAST, event_bus
        if event_bus.has_listeners(EVENT_CHAT_BROADCAST):
            return HealthStatus(
                healthy=True,
                detail="WebUI broadcast channel reachable",
                last_success_at=time.time(),
            )
        return HealthStatus(
            healthy=False,
            detail="WebUI broadcast 无订阅者（web 层未就绪）",
            last_error="no chat_broadcast listener",
        )

    @staticmethod
    async def _broadcast(event: str, data: dict) -> None:
        """经内核事件总线广播 SSE 帧（web 层订阅 EVENT_CHAT_BROADCAST 桥接，
        频道不反向依赖 web 层）。"""
        from core.event_bus import EVENT_CHAT_BROADCAST, event_bus
        await event_bus.emit(EVENT_CHAT_BROADCAST, {
            "event": event,
            "role": "assistant",
            **data,
        })

    @staticmethod
    async def _broadcast_scoped(event: str, payload: dict) -> None:
        """仅转发 WebUI 会话事件，其他频道和内部任务不进入 Web 消息桶。"""
        from agent.messages.everything import parse_entity_scope

        scope = str(payload.get("scope") or "")
        scope_type, adapter, _user_id, session_id = parse_entity_scope(scope)
        if scope_type != "user" or adapter != "webui":
            return
        chat_id = session_id or "default"
        await WebUIChannel._broadcast(event, {**payload, "chat_id": chat_id})
