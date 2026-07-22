"""WebUI 频道 — 接收 Agent 回复并通过 SSE 推送给前端（支持多模态）。"""

from __future__ import annotations

import json
import time
from typing import Any, Set

from pydantic import Field

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import (
    AdapterChannel, ChannelType, SendRequest, SendResponse, SendSegment,
    ChannelInfo, ChannelUser, ChannelUserRole, HealthStatus,
)
from core.log import log




class WebUIConfig(ChannelConfig):
    """网页界面 频道配置。"""
    pass


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

    def __init__(self) -> None:
        super().__init__()

    channel_id = "webui"

    display_name = "网页界面"

    capabilities: Set[ChannelCapability] = {
            ChannelCapability.SEND_TEXT,
            ChannelCapability.SEND_PHOTO,
            ChannelCapability.SEND_VOICE,
            ChannelCapability.SEND_AUDIO,
            ChannelCapability.SEND_VIDEO,
            ChannelCapability.SEND_FILE,
        }

    async def start(self) -> None:
        self._status = ChannelStatus.RUNNING

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        self._broadcast("reply", {"content": text, "media_type": "text"})
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "image",
            "url": photo,
            "caption": caption,
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_voice(self, chat_id: str, voice: str, **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "voice",
            "url": voice,
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_audio(self, chat_id: str, audio: str, caption: str = "", **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "audio",
            "url": audio,
            "caption": caption,
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_video(self, chat_id: str, video: str, caption: str = "", **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "video",
            "url": video,
            "caption": caption,
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_file(self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "file",
            "url": file_path,
            "caption": caption,
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def render_approval_prompt(self, ctx):
        """审批提示 → SSE 弹窗事件（Web 富交互，不发纯文本消息）。"""
        self._broadcast("approval_request", {
            "request_id": ctx.request_id,
            "tool_name": ctx.tool_name,
            "tool_args": ctx.tool_args_summary,
            "risk_level": ctx.risk_level,
            "reason": ctx.reason,
            "timeout_seconds": ctx.timeout_seconds,
        })
        # 返回空片段请求，forward_message 成为 no-op（提示完全由弹窗承担）
        return self._build_empty_request()

    def _build_empty_request(self) -> SendRequest:
        return SendRequest(
            adapter_key=self.channel_id,
            channel=AdapterChannel(
                channel_id="",
                channel_type=ChannelType.PRIVATE,
            ),
            segments=[],
        )

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口。"""
        try:
            for seg in request.segments:
                seg_type = seg.type.value
                if seg_type == "text":
                    self._broadcast("reply", {
                        "content": seg.content,
                        "media_type": "text",
                    })
                elif seg_type in ("image", "voice", "audio", "video", "file"):
                    self._broadcast("media", {
                        "media_type": seg_type,
                        "url": seg.file_path or seg.content,
                        "caption": seg.caption,
                    })
            return SendResponse(success=True, message_id=f"webui-{int(time.time() * 1000)}")
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id="webui_bot",
            user_name="WebUI Bot",
            role=ChannelUserRole.MEMBER,
            is_bot=True,
        )

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id=user_id,
            user_name=user_id,
        )

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        return ChannelInfo(
            channel_id=channel_id,
            channel_name="WebUI Session",
            channel_type=ChannelType.PRIVATE,
        )

    async def health_check(self) -> HealthStatus:
        """WebUI 健康探针：检查 broadcast 函数可达。"""
        try:
            from web.routers.chat import broadcast_chat_event
            return HealthStatus(
                healthy=True,
                detail="WebUI broadcast channel reachable",
                last_success_at=time.time(),
            )
        except ImportError as exc:
            return HealthStatus(
                healthy=False,
                detail=f"WebUI router not available: {exc}",
                last_error=str(exc),
            )

    @staticmethod
    def _broadcast(event: str, data: dict) -> None:
        from web.routers.chat import broadcast_chat_event
        broadcast_chat_event({
            "event": event,
            "role": "assistant",
            **data,
        })
