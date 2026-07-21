"""Telegram Bot 频道 —— 基于 python-telegram-bot 的模块化适配器。

继承 BaseChannel，声明完整能力集，每个能力方法自动注册为 EntityRegistry 工具。
支持长轮询和 Webhook 两种模式，在独立线程中运行自己的 asyncio 事件循环。
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, Optional, Set

from core.log import log

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus, _ok, _err
from agent.channel.tool_bridge import channel_tool
from agent.channel.schemas import (
    AdapterChannel, ChannelType, SendRequest, SendResponse, SendSegment,
    ChannelInfo, ChannelUser, ChannelUserRole, HealthStatus,
)
import time
from pydantic import Field
from .config import TELEGRAM_CONFIGS
from .delivery import deliver_reply


def _fmt_exc(exc: BaseException) -> str:
    """格式化异常为可读消息。

    httpx.ConnectError / telegram.error.NetworkError 包装的连接失败异常
    str() 后消息为空，需特殊处理。
    """
    msg = str(exc).strip()
    if not msg or msg.endswith(": "):
        return f"网络连接失败（{type(exc).__name__}）：频道服务不可达"
    return msg




class TelegramConfig(ChannelConfig):
    """Telegram 频道配置（pydantic 强类型）。"""

    bot_token: str = Field(default="", description="Bot Token（从 @BotFather 获取）")
    proxy_host: str = Field(default="", description="代理地址（留空不使用代理）")
    proxy_port: int = Field(default=7890, description="代理端口")
    require_mention: bool = Field(default=False, description="群聊中是否需要 @Bot 才触发思考")
    reply_to_mode: str = Field(default="first", description="回复引用策略 (first/all/off)")
    stream_mode: str = Field(default="off", description="流式输出模式 (off/draft)")
    parse_mode: str = Field(default="html", description="消息格式化模式 (html/markdown)")
    text_limit: int = Field(default=4096, description="单条消息最大长度")
    link_preview: bool = Field(default=True, description="是否显示链接预览")


class TelegramAdapter(BaseChannel[TelegramConfig]):
    """Telegram Bot 频道（独立线程运行）。"""

    _entity_description = "Telegram Bot 频道"
    _adapter_configs = TELEGRAM_CONFIGS

    metadata = ChannelMetadata(
        name="Telegram",
        description="基于 python-telegram-bot 的 Telegram Bot 频道",
        version="1.0.0",
        author="AnelfAgent",
        tags=["telegram", "chat", "bot"],
    )
    _Configs = TelegramConfig

    def __init__(self) -> None:
        self._app: Optional[Any] = None
        self._bot_username: str = ""
        self._bot_id: Optional[int] = None
        self._tg_loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop_event: Optional[asyncio.Event] = None
        self._start_error: str = ""
        self._known_chats: dict[str, dict] = {}
        super().__init__()

    channel_id = "telegram"

    display_name = "Telegram"

    capabilities: Set[ChannelCapability] = {
            # 发送类
            ChannelCapability.SEND_TEXT,
            ChannelCapability.SEND_PHOTO,
            ChannelCapability.SEND_VIDEO,
            ChannelCapability.SEND_AUDIO,
            ChannelCapability.SEND_VOICE,
            ChannelCapability.SEND_FILE,
            ChannelCapability.SEND_LOCATION,
            ChannelCapability.SEND_ANIMATION,
            ChannelCapability.SEND_CONTACT,
            ChannelCapability.SEND_POLL,
            # 消息操作
            ChannelCapability.EDIT_MESSAGE,
            ChannelCapability.DELETE_MESSAGE,
            ChannelCapability.FORWARD_MESSAGE,
            ChannelCapability.PIN_MESSAGE,
            ChannelCapability.UNPIN_MESSAGE,
            # 信息查询
            ChannelCapability.GET_CHAT_INFO,
            ChannelCapability.GET_CHAT_MEMBERS,
            ChannelCapability.GET_CHAT_ADMINS,
            ChannelCapability.LIST_KNOWN_CHATS,
            # 群管理
            ChannelCapability.BAN_USER,
            ChannelCapability.UNBAN_USER,
            ChannelCapability.SET_CHAT_TITLE,
            ChannelCapability.SET_CHAT_DESCRIPTION,
            # 高级
            ChannelCapability.REPLY_TO,
            ChannelCapability.INLINE_KEYBOARD,
            ChannelCapability.STREAMING,
        }

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        online = self._status.value == "running" and bool(self._bot_username)
        info["online"] = online
        if self._bot_username:
            info["bot_username"] = f"@{self._bot_username}"
        if self._bot_id:
            info["bot_id"] = self._bot_id
        info["detail"] = (
            f"@{self._bot_username} 在线" if online
            else ("连接失败" if self._start_error else "未连接")
        )
        if self._start_error:
            info["error"] = self._start_error
        return info

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            MessageHandler,
            filters,
        )

        token: str = self.config.bot_token
        if not token:
            log("Telegram Bot Token 未配置，频道无法启动", "WARNING")
            self._status = ChannelStatus.ERROR
            return

        proxy_host: str = self.config.proxy_host
        proxy_port: int = int(self.config.proxy_port)

        builder = Application.builder().token(token).connect_timeout(15).read_timeout(30)
        if proxy_host:
            proxy_url = f"http://{proxy_host}:{proxy_port}"
            builder = builder.proxy(proxy_url).get_updates_proxy(proxy_url)
            log(f"Telegram 使用代理: {proxy_url}")

        self._app = builder.build()
        self._app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self._on_message))
        self._app.add_handler(MessageHandler(filters.COMMAND, self._on_command))
        self._app.add_handler(CallbackQueryHandler(self._on_callback_query))

        try:
            from telegram.ext import MessageHandler as MH
            self._app.add_handler(MH(filters.UpdateType.EDITED_MESSAGE, self._on_edited_message))
            self._app.add_handler(MH(filters.UpdateType.CHANNEL_POST, self._on_channel_post))
        except (AttributeError, TypeError):
            pass

        self._ready.clear()
        self._start_error = ""
        self._thread = threading.Thread(
            target=self._run_thread, daemon=True, name="telegram-channel",
        )
        self._thread.start()

        if not self._ready.wait(timeout=30):
            err = self._start_error or "启动超时"
            self._status = ChannelStatus.ERROR
            raise RuntimeError(f"Telegram 频道启动失败: {err}")

        if self._start_error:
            self._status = ChannelStatus.ERROR
            raise RuntimeError(f"Telegram 频道启动失败: {self._start_error}")

        self._status = ChannelStatus.RUNNING
        log(f"Telegram 频道已启动: @{self._bot_username}")

        try:
            from .commands import register_commands
            if self._tg_loop and self._app:
                asyncio.run_coroutine_threadsafe(
                    register_commands(self._app.bot), self._tg_loop,
                )
        except Exception as exc:
            log(f"Telegram 命令菜单注册跳过: {exc}", "DEBUG")

    async def stop(self) -> None:
        if self._tg_loop and self._stop_event:
            self._tg_loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        self._tg_loop = None
        self._app = None
        self._status = ChannelStatus.STOPPED
        log("Telegram 频道已停止")

    # ------------------------------------------------------------------
    # 独立线程
    # ------------------------------------------------------------------

    def _run_thread(self) -> None:
        self._tg_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._tg_loop)
        try:
            self._tg_loop.run_until_complete(self._async_main())
        except Exception as exc:
            log(f"Telegram 线程异常退出: {exc}", "ERROR")
        finally:
            self._tg_loop.close()
            self._tg_loop = None

    async def _async_main(self) -> None:
        assert self._app is not None
        try:
            await self._app.initialize()
        except Exception as exc:
            err = str(exc)
            if "connect" in err.lower() or "network" in err.lower():
                self._start_error = "网络连接失败（请确认代理已启动且可达 api.telegram.org）"
            else:
                self._start_error = err
            self._ready.set()
            return

        await self._app.start()
        await self._app.updater.start_polling(
            allowed_updates=[
                "message", "edited_message", "channel_post",
                "callback_query", "message_reaction",
            ],
            drop_pending_updates=True,
        )

        try:
            bot_info = await self._app.bot.get_me()
            self._bot_username = bot_info.username or ""
            self._bot_id = bot_info.id
        except Exception as exc:
            self._start_error = f"获取 Bot 信息失败: {exc}"
            self._ready.set()
            return

        self._stop_event = asyncio.Event()
        self._ready.set()
        await self._stop_event.wait()

        try:
            if self._app.updater and self._app.updater.running:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        except Exception as exc:
            log(f"Telegram 清理时异常: {exc}", "WARNING")

    # ------------------------------------------------------------------
    # 跨线程执行辅助
    # ------------------------------------------------------------------

    async def _run_in_tg_loop(self, coro: Any) -> Any:
        """在 Telegram 事件循环中执行协程（跨线程非阻塞）。

        从主循环调用时，使用 asyncio.wrap_future 将 concurrent.futures.Future
        转为可 await 的 asyncio.Future，避免 future.result() 阻塞主事件循环线程。
        """
        if not self._app or not self._tg_loop:
            raise RuntimeError("Telegram 频道未就绪")
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._tg_loop:
            return await coro
        fut = asyncio.run_coroutine_threadsafe(coro, self._tg_loop)
        return await asyncio.wrap_future(fut)

    # ------------------------------------------------------------------
    # 能力方法实现（每个返回 JSON，自动注册为工具）
    # ------------------------------------------------------------------

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """通过 Telegram 发送文本消息。"""
        reply_to = kwargs.get("reply_to")
        reply_to_mode = self.config.reply_to_mode
        parse_mode = self.config.parse_mode
        text_limit = int(self.config.text_limit)
        link_preview = bool(self.config.link_preview)

        try:
            async def _do():
                from . import send as tg_send
                await tg_send.send_chat_action(self._app.bot, chat_id, "typing")
                return await deliver_reply(
                    self._app.bot, chat_id, text,
                    reply_to=reply_to,
                    reply_to_mode=reply_to_mode,
                    parse_mode=parse_mode,
                    text_limit=text_limit,
                    link_preview=link_preview,
                )

            result = await self._run_in_tg_loop(_do())
            return _ok({"message_ids": result.message_ids, "chat_id": chat_id})
        except Exception as exc:
            log(f"Telegram send_text 失败: {exc}", "ERROR")
            return _err(_fmt_exc(exc))

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs: Any) -> str:
        """通过 Telegram 发送图片。"""
        try:
            async def _do():
                from . import send as tg_send
                msg_id = await tg_send.send_photo(
                    self._app.bot, chat_id, photo, caption=caption,
                )
                return msg_id

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_video(self, chat_id: str, video: str, caption: str = "", **kwargs: Any) -> str:
        """通过 Telegram 发送视频。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.send_video(self._app.bot, chat_id, video, caption=caption)

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_audio(self, chat_id: str, audio: str, caption: str = "", **kwargs: Any) -> str:
        """通过 Telegram 发送音频。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.send_audio(self._app.bot, chat_id, audio, caption=caption)

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_voice(self, chat_id: str, voice: str, **kwargs: Any) -> str:
        """通过 Telegram 发送语音消息。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.send_voice(self._app.bot, chat_id, voice)

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_file(self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any) -> str:
        """通过 Telegram 发送文件。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.send_file(self._app.bot, chat_id, file_path, caption=caption)

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def send_location(self, chat_id: str, latitude: str, longitude: str, **kwargs: Any) -> str:
        """通过 Telegram 发送地理位置。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.send_location(
                    self._app.bot, chat_id, float(latitude), float(longitude),
                )

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def send_animation(self, chat_id: str, animation: str, caption: str = "", **kwargs: Any) -> str:
        """通过 Telegram 发送 GIF 动图。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.send_animation(self._app.bot, chat_id, animation, caption=caption)

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def edit_message(self, chat_id: str, message_id: str, text: str, **kwargs: Any) -> str:
        """编辑已发送的 Telegram 消息。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.edit_message_text(
                    self._app.bot, chat_id, int(message_id), text,
                )

            ok = await self._run_in_tg_loop(_do())
            return _ok({"edited": ok, "chat_id": chat_id, "message_id": message_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def delete_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """删除 Telegram 消息。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.delete_message(self._app.bot, chat_id, int(message_id))

            ok = await self._run_in_tg_loop(_do())
            return _ok({"deleted": ok, "chat_id": chat_id, "message_id": message_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    # ------------------------------------------------------------------
    # 信息查询 / 群管理 / 交互
    # ------------------------------------------------------------------

    @channel_tool()
    async def send_contact(self, chat_id: str, phone: str, first_name: str, last_name: str = "", **kwargs: Any) -> str:
        """通过 Telegram 发送联系人名片。"""
        try:
            async def _do():
                params = {"chat_id": chat_id, "phone_number": phone, "first_name": first_name}
                if last_name:
                    params["last_name"] = last_name
                msg = await self._app.bot.send_contact(**params)
                return msg.message_id
            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def send_poll(self, chat_id: str, question: str, options: str, **kwargs: Any) -> str:
        """通过 Telegram 发送投票，选项用竖线分隔。"""
        try:
            opts = [o.strip() for o in options.split("|") if o.strip()]
            if len(opts) < 2:
                return _err("投票至少需要 2 个选项，用竖线分隔")
            async def _do():
                msg = await self._app.bot.send_poll(chat_id=chat_id, question=question, options=opts)
                return msg.message_id
            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def forward_msg(self, chat_id: str, from_chat_id: str, message_id: str, **kwargs: Any) -> str:
        """转发 Telegram 消息到另一个会话。"""
        try:
            async def _do():
                msg = await self._app.bot.forward_message(
                    chat_id=chat_id, from_chat_id=from_chat_id, message_id=int(message_id),
                )
                return msg.message_id
            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def pin_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """置顶 Telegram 群组中的消息。"""
        try:
            async def _do():
                await self._app.bot.pin_chat_message(chat_id=chat_id, message_id=int(message_id))
            await self._run_in_tg_loop(_do())
            return _ok({"pinned": True, "chat_id": chat_id, "message_id": message_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def unpin_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """取消置顶 Telegram 群组中的消息。"""
        try:
            async def _do():
                await self._app.bot.unpin_chat_message(chat_id=chat_id, message_id=int(message_id))
            await self._run_in_tg_loop(_do())
            return _ok({"unpinned": True})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def get_chat_info(self, chat_id: str, **kwargs: Any) -> str:
        """查询 Telegram 会话详细信息（标题、类型、成员数等）。"""
        try:
            async def _do():
                chat = await self._app.bot.get_chat(chat_id=chat_id)
                info = {
                    "id": chat.id,
                    "type": chat.type,
                    "title": getattr(chat, "title", None),
                    "username": getattr(chat, "username", None),
                    "first_name": getattr(chat, "first_name", None),
                    "description": getattr(chat, "description", None),
                    "invite_link": getattr(chat, "invite_link", None),
                }
                member_count = getattr(chat, "get_member_count", None)
                if member_count is None:
                    try:
                        info["member_count"] = await self._app.bot.get_chat_member_count(chat_id)
                    except Exception as e:
                        log(f"获取群成员数失败 ({chat_id}): {e}", "DEBUG")
                return info
            info = await self._run_in_tg_loop(_do())
            return _ok(info)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool(description="获取群成员列表（Telegram Bot API 限制，仅返回不可用提示）")
    async def get_chat_members(self, chat_id: str, **kwargs: Any) -> str:
        return _err(
            "Telegram Bot API 不支持直接获取完整成员列表。"
            "可以用 get_chat_admins 获取管理员列表，"
            "或用 get_chat_info 获取成员总数。"
            "Bot 只能看到与它交互过的用户。"
        )

    @channel_tool()
    async def get_chat_admins(self, chat_id: str, **kwargs: Any) -> str:
        """查询 Telegram 群组管理员列表。"""
        try:
            async def _do():
                admins = await self._app.bot.get_chat_administrators(chat_id=chat_id)
                return [
                    {
                        "user_id": str(m.user.id),
                        "name": m.user.full_name or m.user.username or str(m.user.id),
                        "username": m.user.username or "",
                        "status": m.status,
                        "is_bot": m.user.is_bot,
                    }
                    for m in admins
                ]
            admins = await self._run_in_tg_loop(_do())
            return _ok({"admins": admins, "count": len(admins)})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def list_known_chats(self, **kwargs: Any) -> str:
        """列出 Bot 已交互过的所有会话。"""
        if not self._known_chats:
            return _ok({
                "chats": [],
                "hint": "Bot 尚未与任何会话交互。当有人发消息给 Bot 后，会话信息会自动记录。",
            })
        return _ok({"chats": list(self._known_chats.values()), "count": len(self._known_chats)})

    @channel_tool(sensitive=True)
    async def ban_user(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        """封禁 Telegram 群组中的用户。"""
        try:
            async def _do():
                await self._app.bot.ban_chat_member(chat_id=chat_id, user_id=int(user_id))
            await self._run_in_tg_loop(_do())
            return _ok({"banned": True, "chat_id": chat_id, "user_id": user_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def unban_user(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        """解除 Telegram 群组中用户的封禁。"""
        try:
            async def _do():
                await self._app.bot.unban_chat_member(chat_id=chat_id, user_id=int(user_id), only_if_banned=True)
            await self._run_in_tg_loop(_do())
            return _ok({"unbanned": True, "chat_id": chat_id, "user_id": user_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def set_chat_title(self, chat_id: str, title: str, **kwargs: Any) -> str:
        """修改 Telegram 群组标题。"""
        try:
            async def _do():
                await self._app.bot.set_chat_title(chat_id=chat_id, title=title)
            await self._run_in_tg_loop(_do())
            return _ok({"chat_id": chat_id, "title": title})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def set_chat_description(self, chat_id: str, description: str, **kwargs: Any) -> str:
        """修改 Telegram 群组简介描述。"""
        try:
            async def _do():
                await self._app.bot.set_chat_description(chat_id=chat_id, description=description)
            await self._run_in_tg_loop(_do())
            return _ok({"chat_id": chat_id, "description": description[:50]})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    def _record_chat(self, chat_id: str, chat_type: str, title: str = "", username: str = "") -> None:
        """记录已知会话。"""
        self._known_chats[chat_id] = {
            "chat_id": chat_id,
            "type": chat_type,
            "title": title,
            "username": username,
        }

    def _track_chat(self, update: Any) -> None:
        """从 update 中自动记录会话信息。"""
        chat = getattr(update, "effective_chat", None)
        if not chat:
            return
        self._record_chat(
            chat_id=str(chat.id),
            chat_type=getattr(chat, "type", "unknown"),
            title=getattr(chat, "title", "") or "",
            username=getattr(chat, "username", "") or "",
        )

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------

    async def send_message(self, request: Any) -> bool:
        chat_id = request.channel.channel_id
        text = request.content
        reply_to = getattr(request, "reply_to", None)
        result_json = await self.send_text(chat_id, text, reply_to=reply_to)
        result = json.loads(result_json)
        return result.get("success", False)

    # ------------------------------------------------------------------
    # 入站处理器
    # ------------------------------------------------------------------

    async def _on_message(self, update: Any, context: Any) -> None:
        self._track_chat(update)
        from .handlers import handle_message
        require_mention = bool(self.config.require_mention)
        await handle_message(
            update, context,
            bot_username=self._bot_username,
            require_mention=require_mention,
            on_message=self.on_message,
        )

    async def _on_command(self, update: Any, context: Any) -> None:
        from .commands import handle_command
        handled = await handle_command(
            update, context,
            bot_username=self._bot_username,
            bot=self._app.bot if self._app else None,
        )
        if not handled:
            await self._on_message(update, context)

    async def _on_callback_query(self, update: Any, context: Any) -> None:
        from .handlers import handle_callback_query
        await handle_callback_query(update, context, on_message=self.on_message)

    async def _on_edited_message(self, update: Any, context: Any) -> None:
        from .handlers import handle_edited_message
        require_mention = bool(self.config.require_mention)
        await handle_edited_message(
            update, context,
            bot_username=self._bot_username,
            require_mention=require_mention,
            on_message=self.on_message,
        )

    async def _on_channel_post(self, update: Any, context: Any) -> None:
        from .handlers import handle_channel_post
        await handle_channel_post(update, context, on_message=self.on_message)


    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口。"""
        try:
            chat_id = request.channel.channel_id
            reply_to = request.reply_to
            parse_mode = request.parse_mode or self.config.parse_mode
            message_ids: list[str] = []

            for seg in request.segments:
                seg_type = seg.type.value
                if seg_type == "text":
                    result_json = await self.send_text(
                        chat_id, seg.content,
                        reply_to=reply_to, parse_mode=parse_mode,
                    )
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_ids"):
                        message_ids.extend(result["message_ids"])
                    elif result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])
                elif seg_type == "image":
                    result_json = await self.send_photo(
                        chat_id, seg.file_path, caption=seg.caption,
                    )
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])
                elif seg_type == "video":
                    result_json = await self.send_video(
                        chat_id, seg.file_path, caption=seg.caption,
                    )
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])
                elif seg_type == "audio":
                    result_json = await self.send_audio(
                        chat_id, seg.file_path, caption=seg.caption,
                    )
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])
                elif seg_type == "voice":
                    result_json = await self.send_voice(chat_id, seg.file_path)
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])
                elif seg_type == "file":
                    result_json = await self.send_file(
                        chat_id, seg.file_path, caption=seg.caption,
                    )
                    result = json.loads(result_json)
                    if result.get("success") and result.get("message_id"):
                        message_ids.append(result["message_id"])

            if message_ids:
                return SendResponse(
                    success=True,
                    message_id=message_ids[0],
                    message_ids=message_ids,
                )
            return SendResponse(success=True, message_id="empty")
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    async def get_self_info(self) -> ChannelUser:
        """获取 Bot 自身信息。"""
        if not self._app or not self._app.bot:
            raise RuntimeError("Telegram 频道未初始化")
        bot_info = await self._app.bot.get_me()
        return ChannelUser(
            platform=self.channel_id,
            user_id=str(bot_info.id),
            user_name=bot_info.username or "",
            role=ChannelUserRole.MEMBER,
            is_bot=True,
        )

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        """获取用户信息。"""
        if not self._app or not self._app.bot:
            raise RuntimeError("Telegram 频道未初始化")
        try:
            chat_id_int = int(channel_id.split("_", 1)[1]) if "_" in channel_id else int(channel_id)
            if channel_id.startswith("group") or chat_id_int < 0:
                member = await self._app.bot.get_chat_member(chat_id_int, int(user_id))
                user = member.user
                role_map = {
                    "creator": ChannelUserRole.OWNER,
                    "administrator": ChannelUserRole.ADMIN,
                    "member": ChannelUserRole.MEMBER,
                    "restricted": ChannelUserRole.GUEST,
                    "left": ChannelUserRole.GUEST,
                    "kicked": ChannelUserRole.GUEST,
                }
                return ChannelUser(
                    platform=self.channel_id,
                    user_id=str(user.id),
                    user_name=user.full_name or user.username or str(user.id),
                    role=role_map.get(member.status, ChannelUserRole.MEMBER),
                    is_bot=user.is_bot,
                )
            # 私聊
            return ChannelUser(
                platform=self.channel_id,
                user_id=user_id,
                user_name=user_id,
            )
        except Exception as exc:
            log(f"获取用户信息失败 ({user_id}): {exc}", "DEBUG")
            return ChannelUser(
                platform=self.channel_id,
                user_id=user_id,
                user_name=user_id,
            )

    async def get_channel_info(self, channel_id: str) -> ChannelInfo:
        """获取频道信息。"""
        if not self._app or not self._app.bot:
            raise RuntimeError("Telegram 频道未初始化")
        try:
            chat_id_int = int(channel_id.split("_", 1)[1]) if "_" in channel_id else int(channel_id)
            chat = await self._app.bot.get_chat(chat_id_int)
            chat_type = (
                ChannelType.PRIVATE
                if chat.type.value == "private"
                else ChannelType.GROUP
            )
            member_count: Optional[int] = None
            try:
                member_count = await self._app.bot.get_chat_member_count(chat_id_int)
            except Exception:
                pass
            return ChannelInfo(
                channel_id=channel_id,
                channel_name=chat.title or chat.first_name or str(chat_id_int),
                channel_type=chat_type,
                member_count=member_count,
                description=getattr(chat, "description", "") or "",
            )
        except Exception as exc:
            log(f"获取频道信息失败 ({channel_id}): {exc}", "DEBUG")
            chat_type = ChannelType.PRIVATE if channel_id.startswith("private") else ChannelType.GROUP
            return ChannelInfo(
                channel_id=channel_id,
                channel_name=channel_id,
                channel_type=chat_type,
            )

    async def health_check(self) -> HealthStatus:
        """健康探针：调用 get_me 验证 Bot 可达。"""
        if not self._app or not self._app.bot:
            return HealthStatus(
                healthy=False,
                detail="Telegram 频道未初始化",
                last_error="not_initialized",
            )
        try:
            started = time.time()
            await self._app.bot.get_me()
            return HealthStatus(
                healthy=True,
                detail=f"@{self._bot_username} OK",
                latency_ms=(time.time() - started) * 1000,
                last_success_at=time.time(),
            )
        except Exception as exc:
            return HealthStatus(
                healthy=False,
                detail=f"get_me failed: {exc}",
                last_error=str(exc),
            )

    async def render_approval_prompt(self, ctx) -> SendRequest:
        """渲染批准提示（Telegram InlineKeyboard）。"""
        from agent.channel.base import ApprovalPromptRenderContext

        text = (
            f"⚠️ **工具调用需要批准**\n"
            f"\n"
            f"工具: `{ctx.tool_name}`\n"
            f"参数: ```\n{ctx.tool_args_summary}\n```\n"
            f"风险等级: **{ctx.risk_level}**\n"
            f"原因: {ctx.reason}\n"
            f"超时: {ctx.timeout_seconds:.0f}s\n"
        )

        return SendRequest(
            adapter_key=self.channel_id,
            channel=AdapterChannel(
                channel_id="",  # 由 approval/gate.py 填充
                channel_type=ChannelType.PRIVATE,
            ),
            segments=[SendSegment(type="text", content=text)],
            extra={
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "✅ 允许", "callback_data": f"approve:{ctx.request_id}"},
                            {"text": "❌ 拒绝", "callback_data": f"deny:{ctx.request_id}"},
                        ],
                    ],
                },
                "parse_mode": "markdown",
            },
        )
