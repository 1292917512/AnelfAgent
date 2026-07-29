"""QQ 频道 — 通过 OneBot v11 协议对接 NapCat / Lagrange 等 QQ 机器人实现。

支持正向 WebSocket（主动连接）和反向 WebSocket（被动接收）两种模式。

模块拆分：
- transport.py：正向/反向 WS 传输与 OneBot API 调用（QQTransport，注入频道引用）
- send.py：出站消息构造与媒体转换（QQSender，注入频道引用）
- tools.py：表驱动 OneBot API 工具层（QQToolsMixin，经多继承装配）
- parser.py：入站事件解析
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Set

import aiohttp
from aiohttp import web
from pydantic import Field

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import ChannelCapability
from agent.channel.schemas import (
    AdapterChannel,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    SendRequest,
    SendResponse,
    SendSegment,
)
from core.log import log

from .send import QQSender
from .tools import QQToolsMixin
from .transport import QQTransport

if TYPE_CHECKING:
    from agent.channel.base import ApprovalPromptRenderContext


class QQConfig(ChannelConfig):
    """QQ 频道配置（OneBot v11）。"""

    ws_url: str = Field(default="ws://127.0.0.1:3001", description="OneBot WebSocket 地址")
    access_token: str = Field(default="", description="OneBot Access Token")
    require_mention: bool = Field(default=True, description="群聊中是否需要 @Bot 才触发")
    reply_to_mode: str = Field(default="first", description="回复引用策略 (first/all/off)")
    reconnect_interval: int = Field(default=5, description="重连间隔（秒）")


class OneBotV11Channel(QQToolsMixin, BaseChannel[QQConfig]):
    """QQ 频道 — 通过 OneBot v11 协议通信（支持正向/反向 WS）。"""

    _entity_description = "QQ 频道（OneBot v11）"

    metadata = ChannelMetadata(
        name="QQ (OneBot v11)",
        description="基于 OneBot v11 协议的 QQ 频道（通过 NapCat/go-cqhttp 桥接）",
        version="2.0.0",
        author="AnelfAgent",
        tags=["qq", "onebot", "napcat"],
    )
    _Configs = QQConfig

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._listen_task: Optional[asyncio.Task[None]] = None
        self._pending_echoes: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._event_tasks: Set[asyncio.Task[None]] = set()
        self._self_id: str = ""
        self._reverse_runner: Optional[web.AppRunner] = None
        # 白名单缓存（配置加载时构建，消息路径直接命中）
        self._wl_enabled: bool = False
        self._wl_groups: Set[str] = set()
        self._wl_users: Set[str] = set()
        # 传输层与发送器（注入频道引用，避免反向 import）
        self._transport = QQTransport(self)
        self._sender = QQSender(self)
        super().__init__()

    channel_id = "qq"

    display_name = "QQ"

    capabilities: Set[ChannelCapability] = {
            ChannelCapability.SEND_TEXT,
            ChannelCapability.SEND_PHOTO,
            ChannelCapability.SEND_VOICE,
            ChannelCapability.SEND_FILE,
            ChannelCapability.DELETE_MESSAGE,
            ChannelCapability.FORWARD_MESSAGE,
            ChannelCapability.GET_CHAT_INFO,
            ChannelCapability.GET_CHAT_MEMBERS,
            ChannelCapability.BAN_USER,
            ChannelCapability.UNBAN_USER,
            ChannelCapability.SET_CHAT_TITLE,
            ChannelCapability.REPLY_TO,
            ChannelCapability.MESSAGE_REACTION,
        }

    async def start(self) -> None:
        await self._transport.start()

    async def stop(self) -> None:
        await self._transport.stop()

    # ------------------------------------------------------------------
    # 发送路径（委托 QQSender；方法保留在频道上以维持外部调用协议）
    # ------------------------------------------------------------------

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """通过 OneBot v11 发送文本消息，解析 [at_uid:xxx] 并转换为 OneBot at 段。"""
        return await self._sender.send_text(chat_id, text, **kwargs)

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs: Any) -> str:
        """发送图片消息（本地文件自动转 base64）。"""
        return await self._sender.send_photo(chat_id, photo, caption=caption, **kwargs)

    async def send_voice(self, chat_id: str, voice: str, caption: str = "", **kwargs: Any) -> str:
        """发送语音消息（本地文件自动转 base64）。"""
        return await self._sender.send_voice(chat_id, voice, caption=caption, **kwargs)

    async def send_file(self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any) -> str:
        """上传文件到群或私聊，沙盒 EPERM 时回退 base64 上传。"""
        return await self._sender.send_file(chat_id, file_path, caption=caption, **kwargs)

    def is_known_group(self, target_id: str) -> bool:
        """判断 target_id 是否为已知群组（来自白名单配置）。

        用于重启后、尚未收到群消息时的主动发送路由判断。
        """
        self.get_config()  # 确保配置已加载、白名单缓存已构建
        return target_id in self._wl_groups

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        mode = self._cfg("ws_mode", "reverse")
        info["ws_mode"] = mode
        info["ws_connected"] = self._ws is not None and not getattr(self._ws, "closed", True)
        if mode == "reverse":
            info["listen_port"] = int(self._cfg("reverse_ws_port", 8095))
            info["listen_path"] = "/onebot/v11/ws"
            info["detail"] = (
                f"listening on :{info['listen_port']}/onebot/v11/ws"
                + (", client connected" if info["ws_connected"] else ", waiting for client")
            )
        else:
            info["ws_url"] = self._cfg("ws_url", "")
            info["detail"] = (
                f"connected to {info['ws_url']}" if info["ws_connected"]
                else f"connecting to {info['ws_url']}"
            )
        if self._self_id:
            info["self_id"] = self._self_id
        return info

    # ------------------------------------------------------------------
    # 配置读取与白名单
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        """读取配置（使用 self.config）。"""
        return getattr(self.config, key, default)

    def _load_and_register_config(self) -> None:
        """加载配置并重建白名单缓存（覆盖基类，初始加载与热重载都会经过）。"""
        super()._load_and_register_config()
        self._refresh_whitelist_cache()

    def _refresh_whitelist_cache(self) -> None:
        """将白名单配置解析为集合缓存，消息路径避免逐条 split/建 set。"""
        self._wl_enabled = bool(self._cfg("whitelist_enabled", False))
        raw_groups: str = self._cfg("group_whitelist", "")
        raw_users: str = self._cfg("user_whitelist", "")
        self._wl_groups = {g.strip() for g in raw_groups.split(",") if g.strip()}
        self._wl_users = {u.strip() for u in raw_users.split(",") if u.strip()}

    def _check_whitelist(self, data: Dict[str, Any]) -> bool:
        """检查事件来源是否在白名单中。message 和 notice 事件都受白名单控制。"""
        post_type = data.get("post_type")
        if post_type not in ("message", "notice"):
            return True

        self.get_config()  # 确保配置已加载、白名单缓存已构建
        if not self._wl_enabled:
            return True

        if not self._wl_groups and not self._wl_users:
            return True

        group_id = data.get("group_id")
        if group_id is not None:
            return str(group_id) in self._wl_groups

        user_id = data.get("user_id")
        if user_id is not None:
            return str(user_id) in self._wl_users

        return True

    # ------------------------------------------------------------------
    # OneBot API 调用（委托 QQTransport）
    # ------------------------------------------------------------------

    async def _call_api_data(self, action: str, params: Dict[str, Any]) -> Optional[Any]:
        """调用 API 并返回 data 字段，失败返回 None。"""
        return await self._transport.call_api_data(action, params)

    async def _call_api_raw(self, action: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """调用 API 并返回完整响应（HTTP 优先，降级 WS）。"""
        return await self._transport.call_api_raw(action, params)

    async def _call_api(self, action: str, params: Dict[str, Any]) -> bool:
        """调用 OneBot v11 API（优先 HTTP，降级到 WS），返回成功与否。"""
        return await self._transport.call_api(action, params)

    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口（协议）。"""
        try:
            chat_id = request.channel.channel_id
            message_ids: list[str] = []
            for seg in request.segments:
                seg_type = seg.type.value
                if seg_type == "text":
                    result_json = await self.send_text(chat_id, seg.content, reply_to=request.reply_to)
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])
                elif seg_type == "image":
                    result_json = await self.send_photo(chat_id, seg.file_path, caption=seg.caption)
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])
            if message_ids:
                return SendResponse(success=True, message_id=message_ids[0], message_ids=message_ids)
            return SendResponse(success=True, message_id="empty")
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    async def get_self_info(self) -> ChannelUser:
        if not hasattr(self, "_self_id") or not self._self_id:
            raise RuntimeError("QQ 频道未初始化")
        return ChannelUser(
            platform=self.channel_id,
            user_id=str(self._self_id),
            user_name=getattr(self, "_self_nickname", "") or "QQ Bot",
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
            channel_name=channel_id,
            channel_type=self._infer_channel_type(channel_id),
        )

    def _infer_channel_type(self, target_id: str) -> ChannelType:
        """推断会话类型。

        优先使用路由缓存/白名单（数据源自 OneBot 事件的 message_type 字段），
        无协议信息时回退号段启发式（群号通常超过 6 位）并记 DEBUG。
        """
        from agent.channel.manager import get_channel_manager
        if get_channel_manager().resolve_channel_type(self.channel_id, target_id) == "group":
            return ChannelType.GROUP
        heuristic_group = target_id.isdigit() and len(target_id) > 6
        log(f"QQ 会话类型缺少协议字段，回退号段启发式: {target_id} -> "
            f"{'group' if heuristic_group else 'private'}", "DEBUG", tag="通道")
        return ChannelType.GROUP if heuristic_group else ChannelType.PRIVATE

    async def health_check(self) -> HealthStatus:
        """真实探测：调用轻量 OneBot API（get_version_info），超时 5s 判不健康。"""
        http_url = self._cfg("http_api_url", "")
        if not hasattr(self, "_ws") or (self._ws is None and not http_url):
            return HealthStatus(healthy=False, detail="WebSocket not connected", last_error="no_ws")
        started = time.time()
        try:
            result = await asyncio.wait_for(self._call_api_raw("get_version_info", {}), timeout=5.0)
        except Exception as exc:
            return HealthStatus(healthy=False, detail=str(exc), last_error=str(exc))
        latency_ms = (time.time() - started) * 1000
        if result and result.get("retcode") == 0:
            return HealthStatus(
                healthy=True,
                detail=f"QQ OK (self_id={getattr(self, '_self_id', 'unknown')})",
                latency_ms=latency_ms,
                last_success_at=time.time(),
            )
        return HealthStatus(
            healthy=False,
            detail="get_version_info probe failed",
            last_error=str(result),
        )

    async def render_approval_prompt(self, ctx: "ApprovalPromptRenderContext") -> SendRequest:
        """渲染批准提示（QQ 关键词回复）。"""
        text = (
            f"⚠️ 工具调用需要批准\n"
            f"工具: {ctx.tool_name}\n"
            f"参数: {ctx.tool_args_summary[:200]}\n"
            f"风险: {ctx.risk_level}\n"
            f"原因: {ctx.reason}\n"
            f"超时: {ctx.timeout_seconds:.0f}s\n"
            f"\n"
            f"回复以下命令之一：\n"
            f"  approve {ctx.request_id}\n"
            f"  deny {ctx.request_id}"
        )

        return SendRequest(
            adapter_key=self.channel_id,
            channel=AdapterChannel(
                channel_id="",  # 由 approval/gate.py 填充
                channel_type=ChannelType.PRIVATE,
            ),
            segments=[SendSegment(type="text", content=text)],
        )
