"""WebUI 频道 — 接收 Agent 回复并通过 SSE 推送给前端（支持多模态）。"""

from __future__ import annotations

import json
from typing import Any, Set

from agent.channel.channel import BaseChannel, ChannelCapability, ChannelStatus
from core.log import log


class WebUIChannel(BaseChannel):
    """WebUI 频道 — 通过 SSE 向前端推送 Agent 消息（文本/图片/语音/视频）。"""

    _entity_description = "网页界面多媒体频道"

    def __init__(self) -> None:
        super().__init__()

    @property
    def channel_id(self) -> str:
        return "webui"

    @property
    def display_name(self) -> str:
        return "网页界面"

    @property
    def capabilities(self) -> Set[ChannelCapability]:
        return {
            ChannelCapability.SEND_TEXT,
            ChannelCapability.SEND_PHOTO,
            ChannelCapability.SEND_VOICE,
            ChannelCapability.SEND_AUDIO,
            ChannelCapability.SEND_VIDEO,
            ChannelCapability.SEND_DOCUMENT,
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

    async def send_document(self, chat_id: str, document: str, caption: str = "", **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "file",
            "url": document,
            "caption": caption,
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    @staticmethod
    def _broadcast(event: str, data: dict) -> None:
        from web.routers.chat import broadcast_chat_event
        broadcast_chat_event({
            "event": event,
            "role": "assistant",
            **data,
        })
