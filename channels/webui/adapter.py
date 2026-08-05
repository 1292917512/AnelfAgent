"""WebUI 频道 — 接收 Agent 回复并通过 SSE 推送给前端（支持多模态）。"""

from __future__ import annotations

import importlib.util
import json
import time
from typing import Any, Set

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus
from agent.channel.schemas import (
    AdapterChannel,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    SendRequest,
    SendResponse,
)
from core.tags import strip_functional_tags, strip_message_meta_tags


def _clean_outbound(text: str) -> str:
    """出站文本清洗：剥离元数据标签与功能性标签（对齐历史清洗语义）。"""
    return strip_functional_tags(strip_message_meta_tags(text or "")).strip()


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
        from core.event_bus import EVENT_AFTER_REPLY, event_bus
        from core.stream_events import EVENT_ASSISTANT_DELTA
        event_bus.on(EVENT_ASSISTANT_DELTA, self._on_assistant_delta, owner="channel:webui")
        event_bus.on("thinking_tool_start", self._on_tool_start, owner="channel:webui")
        event_bus.on("thinking_tool_end", self._on_tool_end, owner="channel:webui")
        event_bus.on(EVENT_AFTER_REPLY, self._on_after_reply, owner="channel:webui")
        from core.stream_events import EVENT_CONTEXT_USAGE, EVENT_FILE_DIFF
        event_bus.on(EVENT_FILE_DIFF, self._on_file_diff, owner="channel:webui")
        event_bus.on(EVENT_CONTEXT_USAGE, self._on_context_usage, owner="channel:webui")
        # Plan 模式 / 子代理：直接广播到前端（PlanPanel / PlanCard / DelegationCard）。
        # 事件名与 SSE 帧名一致，表驱动注册（handler 统一为 _broadcast_scoped 转发）。
        from core.event_bus import (
            EVENT_DELEGATION_PROGRESS,
            EVENT_DELEGATION_RESOLVED,
            EVENT_DELEGATION_STARTED,
            EVENT_PLAN_CANCELLED,
            EVENT_PLAN_STATUS_CHANGED,
            EVENT_PLAN_STEP_UPDATED,
            EVENT_PLAN_SUBMITTED,
        )
        for evt in (
            EVENT_PLAN_SUBMITTED, EVENT_PLAN_STEP_UPDATED,
            EVENT_PLAN_STATUS_CHANGED, EVENT_PLAN_CANCELLED,
            EVENT_DELEGATION_STARTED, EVENT_DELEGATION_PROGRESS, EVENT_DELEGATION_RESOLVED,
        ):
            event_bus.on(evt, self._make_scoped_forwarder(evt), owner="channel:webui")

    def _make_scoped_forwarder(self, event: str):
        """生成把内核事件透传到 SSE 的 handler（帧名与事件名一致）。"""
        async def _forward(payload: dict) -> None:
            self._broadcast_scoped(event, payload)
        return _forward

    async def _on_after_reply(self, payload: dict) -> None:
        """轮次结束 → turn_end 帧（前端清除发送态/流式气泡）。

        覆盖无 reply 帧的结束路径（[SILENT] 沉默、空输出、异常），
        避免前端 sending 状态空等看门狗超时。
        """
        self._broadcast_scoped("turn_end", {"scope": payload.get("scope", ""), "error": bool(payload.get("error"))})

    async def _on_assistant_delta(self, payload: dict) -> None:
        """assistant 文本增量 → 50ms 合帧后推送 SSE delta 帧。"""
        import asyncio
        turn_id = str(payload.get("turn_id", ""))
        buf = self._delta_buffers.setdefault(
            turn_id, {"text": "", "reasoning": "", "scope": "", "scheduled": False})
        if payload.get("scope"):
            buf["scope"] = str(payload["scope"])
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
        scope = buf.get("scope", "")
        buf["text"] = buf["reasoning"] = ""
        if reasoning:
            self._broadcast_scoped("delta", {"scope": scope, "turn_id": turn_id, "delta": reasoning, "reasoning": True})
        if text:
            self._broadcast_scoped("delta", {"scope": scope, "turn_id": turn_id, "delta": text, "reasoning": False})

    async def _on_tool_start(self, payload: dict) -> None:
        self._broadcast_scoped("tool_call", {
            "scope": payload.get("scope", ""),
            "call_id": payload.get("tool_id", ""),
            "name": payload.get("tool_name", ""),
            "status": "running",
            "arguments": payload.get("arguments_preview", ""),
        })

    async def _on_context_usage(self, payload: dict) -> None:
        self._broadcast_scoped("context_usage", {
            "scope": payload.get("scope", ""),
            "tokens": payload.get("tokens", 0),
            "threshold": payload.get("threshold", 0),
            "window": payload.get("window", 0),
            "percent": payload.get("percent", 0),
            "cache_read_input_tokens": payload.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": payload.get("cache_creation_input_tokens", 0),
            "cache_hit_rate": payload.get("cache_hit_rate", 0.0),
        })

    async def _on_file_diff(self, payload: dict) -> None:
        self._broadcast("file_diff", {
            "path": payload.get("path", ""),
            "diff": payload.get("diff", ""),
            "additions": payload.get("additions", 0),
            "removals": payload.get("removals", 0),
        })

    async def _on_tool_end(self, payload: dict) -> None:
        self._broadcast_scoped("tool_call", {
            "scope": payload.get("scope", ""),
            "call_id": payload.get("tool_id", ""),
            "name": payload.get("tool_name", ""),
            "status": "done" if payload.get("success") else "error",
            "result_preview": payload.get("result_preview", "") or payload.get("error", ""),
            "duration_ms": payload.get("duration_ms", 0),
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

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        text = _clean_outbound(text)
        if not text:
            return json.dumps({"success": True}, ensure_ascii=False)
        self._broadcast("reply", {
            "content": text,
            "media_type": "text",
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "image",
            "url": photo,
            "caption": _clean_outbound(caption),
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_voice(self, chat_id: str, voice: str, **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "voice",
            "url": voice,
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_audio(self, chat_id: str, audio: str, caption: str = "", **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "audio",
            "url": audio,
            "caption": _clean_outbound(caption),
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_video(self, chat_id: str, video: str, caption: str = "", **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "video",
            "url": video,
            "caption": _clean_outbound(caption),
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
        })
        return json.dumps({"success": True}, ensure_ascii=False)

    async def send_file(self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any) -> str:
        self._broadcast("media", {
            "media_type": "file",
            "url": file_path,
            "caption": _clean_outbound(caption),
            "chat_id": self._resolve_chat_id(chat_id, kwargs),
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
        """WebUI 健康探针：检查 broadcast 路由模块可达。"""
        try:
            if importlib.util.find_spec("web.routers.chat") is None:
                raise ImportError("web.routers.chat 模块不存在")
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

    @staticmethod
    def _broadcast_scoped(event: str, payload: dict) -> None:
        """带 scope/chat_id 的广播：从 payload.scope 解析 chat_id 一并推给前端，
        前端 buckets[chat_id] 据此路由。"""
        scope = str(payload.get("scope") or "")
        chat_id = str(payload.get("chat_id") or "")
        if not chat_id and "#" in scope:
            chat_id = scope.split("#", 1)[1]
        WebUIChannel._broadcast(event, {**payload, "chat_id": chat_id})
