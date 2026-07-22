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
        # 流式 delta 合帧缓冲（turn_id → 待发送增量）
        self._delta_buffers: dict = {}

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
        self._subscribe_stream_events()

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED
        from core.event_bus import event_bus
        event_bus.off_by_owner("channel:webui")

    # ------------------------------------------------------------------
    # 流式过程事件订阅（内核事件 → SSE 帧；过程性内容，不落对话历史）
    # ------------------------------------------------------------------

    def _subscribe_stream_events(self) -> None:
        from core.event_bus import event_bus
        from core.stream_events import EVENT_ASSISTANT_DELTA
        event_bus.on(EVENT_ASSISTANT_DELTA, self._on_assistant_delta, owner="channel:webui")
        event_bus.on("thinking_tool_start", self._on_tool_start, owner="channel:webui")
        event_bus.on("thinking_tool_end", self._on_tool_end, owner="channel:webui")
        from core.stream_events import EVENT_FILE_DIFF, EVENT_CONTEXT_USAGE
        event_bus.on(EVENT_FILE_DIFF, self._on_file_diff, owner="channel:webui")
        event_bus.on(EVENT_CONTEXT_USAGE, self._on_context_usage, owner="channel:webui")

    async def _on_assistant_delta(self, payload: dict) -> None:
        """assistant 文本增量 → 50ms 合帧后推送 SSE delta 帧。"""
        import asyncio
        turn_id = str(payload.get("turn_id", ""))
        buf = self._delta_buffers.setdefault(
            turn_id, {"text": "", "reasoning": "", "scheduled": False})
        key = "reasoning" if payload.get("reasoning") else "text"
        buf[key] += str(payload.get("delta", ""))
        if not buf["scheduled"]:
            buf["scheduled"] = True
            asyncio.get_running_loop().call_later(0.05, self._flush_delta, turn_id)

    def _flush_delta(self, turn_id: str) -> None:
        buf = self._delta_buffers.get(turn_id)
        if not buf:
            return
        buf["scheduled"] = False
        text, reasoning = buf["text"], buf["reasoning"]
        buf["text"] = buf["reasoning"] = ""
        if reasoning:
            self._broadcast("delta", {"turn_id": turn_id, "delta": reasoning, "reasoning": True})
        if text:
            self._broadcast("delta", {"turn_id": turn_id, "delta": text, "reasoning": False})

    async def _on_tool_start(self, payload: dict) -> None:
        self._broadcast("tool_call", {
            "call_id": payload.get("tool_id", ""),
            "name": payload.get("tool_name", ""),
            "status": "running",
            "arguments": payload.get("arguments_preview", ""),
        })

    async def _on_context_usage(self, payload: dict) -> None:
        self._broadcast("context_usage", {
            "tokens": payload.get("tokens", 0),
            "threshold": payload.get("threshold", 0),
            "window": payload.get("window", 0),
            "percent": payload.get("percent", 0),
        })

    async def _on_file_diff(self, payload: dict) -> None:
        self._broadcast("file_diff", {
            "path": payload.get("path", ""),
            "diff": payload.get("diff", ""),
            "additions": payload.get("additions", 0),
            "removals": payload.get("removals", 0),
        })

    async def _on_tool_end(self, payload: dict) -> None:
        self._broadcast("tool_call", {
            "call_id": payload.get("tool_id", ""),
            "name": payload.get("tool_name", ""),
            "status": "done" if payload.get("success") else "error",
            "result_preview": payload.get("result_preview", "") or payload.get("error", ""),
            "duration_ms": payload.get("duration_ms", 0),
        })

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
