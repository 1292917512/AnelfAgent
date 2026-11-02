"""QQ 开放平台机器人，接入统一频道发现、生命周期、投递与工具注册。

模型经 Anelf 消息管线看到单聊、群聊、频道消息、附件和引用摘要。
消息与按需发现的频道工具增加动态 token，大小取决于正文及附件。
使用既有入站和出站层，不改写稳定前缀。
"""

import asyncio
import time
from collections import OrderedDict
from contextlib import suppress
from typing import Any
from urllib.parse import quote

from agent.channel import BaseChannel, ChannelMetadata, async_network_client, channel_tool
from agent.channel.channel_types import ChannelCapability, ChannelStatus, _ok
from agent.channel.dedup import MessageDedup
from agent.channel.schemas import (
    AdapterMessage,
    ChannelInfo,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    MessageKind,
    SendRequest,
    SendResponse,
    SendSegment,
)
from agent.messages import build_scope_id
from core.log import log
from core.sanitizer import sanitize_text
from core.tool_errors import error_from_exception

from .client import QQAPI, QQAPIError
from .config import QQBotConfig
from .events import Target, identifier, parse_event, reference_id
from .gateway import Gateway
from .send import Replies, Sender
from .tools import QQBotTools
from .webhook import CALLBACK_PATH, Webhook
from .webhook import build_router as build_router


class QQBotChannel(QQBotTools, BaseChannel[QQBotConfig]):
    channel_id = "qqbot"
    display_name = "QQ 官方机器人"
    display_order = 21
    metadata = ChannelMetadata(name=display_name, description="通过 QQ 开放平台收发群、用户和频道消息",
                               author="AnelfAgent", tags=["qq", "qqbot"])
    _Configs = QQBotConfig
    capabilities = {
        ChannelCapability.SEND_TEXT, ChannelCapability.SEND_PHOTO, ChannelCapability.SEND_VOICE,
        ChannelCapability.SEND_AUDIO, ChannelCapability.SEND_VIDEO, ChannelCapability.SEND_FILE,
        ChannelCapability.REPLY_TO, ChannelCapability.DELETE_MESSAGE, ChannelCapability.GET_CHAT_INFO,
        ChannelCapability.GET_CHAT_MEMBERS, ChannelCapability.LIST_KNOWN_CHATS, ChannelCapability.INLINE_KEYBOARD,
        ChannelCapability.MESSAGE_REACTION,
        ChannelCapability.PIN_MESSAGE, ChannelCapability.UNPIN_MESSAGE,
    }

    def __init__(self) -> None:
        self._api: QQAPI | None = None
        self._sender: Sender | None = None
        self._gateway: Gateway | None = None
        self._task: asyncio.Task[None] | None = None
        self.webhook: Webhook | None = None
        self._active_config: QQBotConfig | None = None
        self._self_id = ""
        self._self_name = ""
        self._start_error = ""
        self._state_app_id = ""
        self._replies = Replies()
        self._gate = asyncio.Lock()
        self._dedup = MessageDedup(ttl_seconds=3600, max_size=8192)
        self._confirmed_interactions = MessageDedup(ttl_seconds=3600, max_size=8192)
        self._known_chats: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._last_event_at = 0.0
        super().__init__()

    async def start(self) -> None:
        if self._api is not None:
            return
        cfg = self.config
        self._start_error = ""
        self._last_event_at = 0.0
        if not cfg.app_id.strip() or not cfg.app_secret.strip():
            self._start_error = "QQ 官方频道需要 AppID 和 AppSecret"
            raise ValueError(self._start_error)
        self._active_config = cfg
        self._api = QQAPI(async_network_client(timeout=20), cfg.app_id, cfg.app_secret)
        try:
            user = await self.get_self_info()
            self._self_id, self._self_name = user.user_id, user.user_name
            if self._status == ChannelStatus.ERROR:
                raise RuntimeError("QQ 启动期间连接配置已变化")
            if self._state_app_id != cfg.app_id:
                self._state_app_id = cfg.app_id
                self._replies = Replies()
                self._dedup = MessageDedup(ttl_seconds=3600, max_size=8192)
                self._confirmed_interactions = MessageDedup(ttl_seconds=3600, max_size=8192)
                self._known_chats.clear()
            self._sender = Sender(self._api, self._replies)
            if cfg.connection_mode == "webhook":
                self.webhook = Webhook(cfg.app_id, cfg.app_secret)
                self._status = ChannelStatus.RUNNING
            else:
                self._gateway = Gateway(self._api, cfg.intents, self.dispatch, self._gateway_status)
                self._gateway_status(ChannelStatus.RECONNECTING)
                self._task = asyncio.create_task(self._gateway.run(), name="channel.qqbot.gateway")
        except (Exception, asyncio.CancelledError) as exc:
            self._start_error = sanitize_text(str(exc).replace(cfg.app_secret, "***"))[:300]
            await self.stop()
            raise

    def _gateway_status(self, status: ChannelStatus) -> None:
        if self._status != ChannelStatus.ERROR:
            self._status = status

    async def stop(self) -> None:
        self.webhook = None
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        self._task = self._gateway = None
        async with self._gate:
            if self._api:
                await self._api.http.aclose()
            self._api = self._sender = self._active_config = None
            self._self_id = self._self_name = ""
            self._status = ChannelStatus.STOPPED

    def _on_config_changed(self, key: str, value: Any) -> None:
        super()._on_config_changed(key, value)
        old = self._active_config
        if old and any(getattr(old, f) != getattr(self.config, f)
                       for f in ("app_id", "app_secret", "connection_mode", "intents")):
            self.webhook = None
            self._status = ChannelStatus.ERROR  # 由 ChannelSupervisor 按频道生命周期重新装配。

    @property
    def api(self) -> QQAPI:
        if self._api is None or self.status == ChannelStatus.ERROR:
            raise RuntimeError("QQ 官方频道未就绪")
        return self._api

    async def dispatch(self, payload: dict[str, Any], *, receiver: Webhook | None = None) -> None:
        async with self._gate:
            if not self._sender or self.status == ChannelStatus.ERROR or (receiver and receiver is not self.webhook):
                raise RuntimeError("QQ 频道未就绪")
            try:
                message = parse_event(payload, self._self_id, self.config.require_mention, self._self_name)
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                log(f"QQ 事件字段无效 ({payload.get('t')}): {type(exc).__name__}", "WARNING", tag="通道")
                return
            if message is None:
                return
            address = message.channel.channel_id
            allowed = self.config.allowed_chats.strip()
            if allowed and address not in {s.strip() for s in allowed.split(",")}:
                return
            data = payload["d"]
            interaction_id = data["id"] if payload["t"] == "INTERACTION_CREATE" and data.get("type") in (11, 12) else ""
            ref = reference_id(data) if message.kind == MessageKind.CHAT else ""
            identity = interaction_id or message.message_id
            key = f"{address}/{message.kind.value}/{identity}"
            idx_key = f"{address}/msg_idx/{ref}" if ref else ""
            duplicate = self._dedup.is_duplicate(key)
            if not duplicate and idx_key:
                duplicate = self._dedup.is_duplicate(idx_key)
            if not duplicate:
                try:
                    if address.startswith("channel.") and message.reply_to_id and message.kind == MessageKind.CHAT:
                        await self._guild_quote(message)
                    await self.on_message(message)
                except (Exception, asyncio.CancelledError):
                    self._dedup.discard(key)
                    self._dedup.discard(idx_key)
                    raise
                self._last_event_at = time.time()
                if message.kind == MessageKind.CHAT or message.message_id.startswith("event."):
                    self._replies.remember(address, message.message_id, message.timestamp, ref)
                if message.kind != MessageKind.NOTIFICATION:
                    self._known_chats[address] = {"chat_id": address, "type": message.channel.channel_type.value}
                    self._known_chats.move_to_end(address)
                    if len(self._known_chats) > 2048:
                        self._known_chats.popitem(last=False)
            if interaction_id and not self._confirmed_interactions.is_duplicate(interaction_id):
                try:
                    async with asyncio.timeout(3):
                        await self.api.request("PUT", "/interactions/" + quote(interaction_id, safe=""), json={"code": 0})
                except QQAPIError as exc:
                    if exc.status >= 500 or exc.status == 429 or exc.code in ("630004", "630005"):
                        self._confirmed_interactions.discard(interaction_id)
                        raise
                    log(f"QQ 互动回应被平台拒绝: {exc}", "WARNING", tag="通道")
                except (Exception, asyncio.CancelledError):
                    self._confirmed_interactions.discard(interaction_id)
                    raise

    async def _guild_quote(self, message: AdapterMessage) -> None:
        path = Target.parse(message.channel.channel_id).base + "/messages/" + quote(message.reply_to_id, safe="")
        try:
            async with asyncio.timeout(2):
                response = await self.api.request("GET", path)
            original = response.get("message", response)
            message.reply_content = str(original.get("content") or "")[:200]
            if (original.get("author") or {}).get("id") == self._self_id:
                message.is_to_me = message.trigger_mind = True
        except Exception as exc:
            log(f"QQ 子频道引用查询失败: {exc}", "DEBUG", tag="通道")

    @staticmethod
    def is_known_group(target_id: str) -> bool:
        return target_id.startswith(("group.", "channel."))

    def conversation_scope_for_target(self, target_id: str, channel_type: str) -> tuple[str, str] | None:
        if target_id.startswith("dm."):
            target = Target.parse(target_id)
            return "user", build_scope_id(self.channel_id, target.peer, "#" + target_id)
        return None

    async def forward_message(self, request: SendRequest) -> SendResponse:
        if self._sender is None or self.status == ChannelStatus.ERROR:
            return SendResponse(success=False, error="QQ 官方频道未就绪")
        try:
            return await self._sender.send(request, self.config.message_max_length,
                                           gateway_online=bool(self._gateway and self._gateway.online))
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    async def send_text(self, chat_id: str, text: str, *, reply_to: str | None = None,
                        parse_mode: str | None = None, silent: bool = False, channel_type: str = "private",
                        **kwargs: Any) -> str:
        request = self._build_send_request(chat_id, [SendSegment(content=text)], reply_to=reply_to,
                                           parse_mode=parse_mode, silent=silent, channel_type=channel_type,
                                           extra=kwargs)
        response = await self.forward_message(request)
        return response.model_dump_json(exclude_none=True)

    def _media_tool_result(self, resp: SendResponse) -> str:
        return resp.model_dump_json(exclude_none=True)

    @channel_tool(outbound=True, description="向 QQ 会话发送 Markdown，可附带开放平台键盘模板")
    async def send_markdown(self, chat_id: str, content: str, keyboard_id: str = "", session_id: str = "") -> str:
        """通过统一出站管道发送 Markdown，键盘模板需要平台已开通相应权限。"""
        return await self.send_text(chat_id, content, parse_mode="markdown", session_id=session_id,
                                    keyboard_id=keyboard_id)

    @channel_tool(outbound=True, description="向 QQ 单聊或群聊发送带封面和跳转链接的图文卡片")
    async def send_card(self, chat_id: str, title: str, content: str, url: str, pic_url: str, session_id: str = "") -> str:
        """标题最多 50 字，正文最多 200 字，跳转链接和封面均使用 HTTPS。"""
        return await self.send_text(chat_id, content, session_id=session_id,
                                    card={"title": title, "url": url, "pic_url": pic_url})

    async def get_self_info(self) -> ChannelUser:
        data = await self.api.request("GET", "/users/@me")
        if not data.get("id"):
            raise RuntimeError("QQ 未返回机器人用户 ID")
        return ChannelUser(platform=self.channel_id, user_id=data["id"], user_name=data.get("username") or "",
                           avatar=data.get("avatar") or "", is_bot=True)

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        target = Target.parse(channel_id)
        data: dict[str, Any] = {}
        if target.kind == "group":
            data = await self.api.request("GET", target.base + "/info")
        elif target.kind == "channel":
            data = await self.api.request("GET", target.base)
        return ChannelInfo(channel_id=channel_id, channel_type=target.channel.channel_type,
                           channel_name=data.get("group_name") or data.get("name") or channel_id,
                           member_count=data.get("group_member_num"), description=data.get("group_finger_memo") or "",
                           extra={"guild_id": data["guild_id"]} if data.get("guild_id") else {})

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        target = Target.parse(channel_id)
        if target.kind == "group":
            data = await self.api.request("GET", f"{target.base}/members/{identifier(user_id)}")
            return ChannelUser(platform=self.channel_id, user_id=user_id, user_name=data.get("username") or user_id,
                               role=ChannelUserRole(data.get("member_role") or "member"), is_bot=bool(data.get("bot")))
        if target.kind == "channel":
            channel = await self.get_channel_info(channel_id)
            guild = identifier(channel.extra.get("guild_id"))
            member = await self.api.request("GET", f"/guilds/{guild}/members/{identifier(user_id)}")
            user = member["user"]
            return ChannelUser(platform=self.channel_id, user_id=user_id, user_name=member.get("nick") or user.get("username") or user_id,
                               avatar=user.get("avatar") or "", is_bot=bool(user.get("bot")), extra={"roles": member.get("roles") or []})
        return await super().get_user_info(user_id, channel_id)

    @channel_tool(description="按 QQ 群或子频道目标查成员资料")
    async def get_member_info(self, chat_id: str, user_id: str) -> str:
        try:
            if Target.parse(chat_id).kind not in ("group", "channel"):
                raise ValueError("成员查询需要群聊或子频道目标")
            return _ok((await self.get_user_info(user_id, chat_id)).model_dump())
        except Exception as exc:
            return error_from_exception(exc, action="查询 QQ 成员资料", retry_safe=True)

    @channel_tool(description="查询 QQ 官方机器人会话信息")
    async def get_chat_info(self, chat_id: str, session_id: str = "") -> str:
        """查询单个会话的类型和可用的群组资料。"""
        try:
            target = Target.for_session(chat_id, session_id)
            return _ok((await self.get_channel_info(target.address)).model_dump())
        except Exception as exc:
            return error_from_exception(exc, action="查询 QQ 会话", retry_safe=True)

    @channel_tool(description="按游标列出 QQ 群成员，需平台授予群管理权限")
    async def get_chat_members(self, chat_id: str, cursor: str = "") -> str:
        """查询一页 QQ 群成员。

        Args:
            chat_id: group.ID 格式的目标。
            cursor: 上一页返回的 next_cursor。
        """
        try:
            target = Target.parse(chat_id)
            if target.kind != "group":
                raise ValueError("需要 group.ID 目标")
            data = await self.api.request("GET", target.base + "/members", params={"cursor": cursor} if cursor else {})
            return _ok(data)
        except Exception as exc:
            return error_from_exception(exc, action="查询 QQ 群成员", retry_safe=True)

    @channel_tool(description="撤回 QQ 机器人在指定会话发出的消息", sensitive=True)
    async def delete_message(self, chat_id: str, message_id: str, session_id: str = "") -> str:
        """撤回已确认送达的机器人消息。

        Args:
            chat_id: 原消息所在会话。
            message_id: QQ 发送回执中的消息 ID。
            session_id: 频道私信的会话目标。
        """
        try:
            if not message_id or message_id in (".", ".."):
                raise ValueError("需要 QQ 消息 ID")
            target = Target.for_session(chat_id, session_id)
            await self.api.request("DELETE", target.base + "/messages/" + quote(message_id, safe=""))
            return _ok({"message_id": message_id})
        except Exception as exc:
            return error_from_exception(exc, action="撤回 QQ 消息")

    async def health_check(self) -> HealthStatus:
        if self._gateway and not self._gateway.online:
            return HealthStatus(healthy=False, detail="QQ 网关未登录", last_error=self._gateway.last_error)
        try:
            user = await self.get_self_info()
            return HealthStatus(healthy=True, detail=f"QQ API 已连接 ({user.user_name or user.user_id})")
        except Exception as exc:
            return HealthStatus(healthy=False, detail="QQ API 探测失败", last_error=str(exc))

    def get_status_info(self) -> dict[str, Any]:
        info = super().get_status_info()
        info.update(connection_mode=self.config.connection_mode,
                    active_mode=self._active_config.connection_mode if self._active_config else None,
                    self_id=self._self_id, bot_name=self._self_name,
                    last_error=self._start_error if self.status == ChannelStatus.ERROR else "",
                    last_event_at=self._last_event_at, webhook_path=CALLBACK_PATH)
        if self._gateway:
            info.update(gateway_online=self._gateway.online, last_error=sanitize_text(self._gateway.last_error))
        return info


CHANNEL_CLASS = QQBotChannel
