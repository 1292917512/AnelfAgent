"""Telegram Bot 频道 —— 基于 python-telegram-bot 的模块化适配器。

继承 BaseChannel，声明完整能力集，每个能力方法自动注册为 EntityRegistry 工具。
长轮询模式，在独立线程中运行自己的 asyncio 事件循环。
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional, Set

from agent.channel.base import BaseChannel, ChannelMetadata
from agent.channel.channel_types import ChannelCapability, ChannelStatus, _err, _ok
from agent.channel.schemas import (
    AdapterChannel,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    HealthStatus,
    SegmentType,
    SendRequest,
    SendResponse,
    SendSegment,
)
from agent.channel.tool_bridge import channel_tool
from agent.channel.utils.formatter import format_exception as _fmt_exc
from core.log import log
from core.sanitizer import sanitize_text

from .config import TelegramConfig
from .delivery import deliver_reply

if TYPE_CHECKING:
    from telegram.request import HTTPXRequest

    from agent.channel.base import ApprovalPromptRenderContext

_STARTUP_TIMEOUT = 30.0


class TelegramAdapter(BaseChannel[TelegramConfig]):
    """Telegram Bot 频道（独立线程运行）。"""

    _entity_description = "Telegram Bot 频道"

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
        self._stop_requested = threading.Event()
        self._stop_event: Optional[asyncio.Event] = None
        self._main_task: Optional[asyncio.Task[None]] = None
        self._lifecycle_lock = asyncio.Lock()
        self._requests: tuple[HTTPXRequest, ...] = ()
        self._started = False
        self._stopping = False
        self._start_stage = "准备启动"
        self._start_error: str = ""
        self._known_chats: OrderedDict[str, dict] = OrderedDict()
        super().__init__()

    channel_id = "telegram"

    display_name = "Telegram"

    display_order = 22

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
        async with self._lifecycle_lock:
            try:
                await self._start_locked()
            except BaseException:
                self._status = ChannelStatus.ERROR
                if self._thread is not None:
                    await self._stop_polling_thread()
                else:
                    await self._cleanup_application(self._app)
                    self._app = None
                    self._requests = ()
                raise

    async def _start_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            if self._started and not self._stop_requested.is_set():
                self._status = ChannelStatus.RUNNING
                return
            raise RuntimeError("Telegram 旧轮询线程尚未退出，拒绝创建重复轮询")

        from telegram.ext import (
            Application,
            CallbackQueryHandler,
            MessageHandler,
            filters,
        )
        from telegram.request import HTTPXRequest

        token: str = self.config.bot_token
        if not token:
            raise RuntimeError("Telegram Bot Token 未配置，频道无法启动")

        proxy_host: str = self.config.proxy_host
        proxy_port: int = int(self.config.proxy_port)

        self._thread = None
        self._requests = ()
        proxy_url = f"http://{proxy_host}:{proxy_port}" if proxy_host else None
        request = HTTPXRequest(
            connect_timeout=15, read_timeout=30, proxy=proxy_url,
        )
        self._requests = (request,)
        polling_request = HTTPXRequest(
            connection_pool_size=1, proxy=proxy_url,
        )
        self._requests = (request, polling_request)
        self._app = (Application.builder().token(token)
                     .request(request).get_updates_request(polling_request).build())
        self._app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, self._on_message))
        self._app.add_handler(MessageHandler(filters.COMMAND, self._on_command))
        self._app.add_handler(CallbackQueryHandler(self._on_callback_query))

        try:
            self._app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, self._on_edited_message))
            self._app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, self._on_channel_post))
        except (AttributeError, TypeError):
            log("start 异常已忽略", "DEBUG")

        self._ready.clear()
        self._stop_requested.clear()
        self._stop_event = None
        self._started = False
        self._stopping = False
        self._start_stage = "初始化/getMe"
        self._start_error = ""
        self._bot_username = ""
        self._bot_id = None
        self._thread = threading.Thread(
            target=self._run_thread, daemon=True, name="telegram-channel",
        )
        self._thread.start()

        ready = await asyncio.to_thread(self._ready.wait, _STARTUP_TIMEOUT)
        if not ready:
            self._start_error = self._format_startup_error(TimeoutError("启动总等待时间已耗尽"))

        if self._start_error or not self._started:
            self._start_error = self._start_error or "轮询线程在就绪前已退出"
            raise RuntimeError(f"Telegram 频道启动失败: {self._start_error}")

        self._status = ChannelStatus.RUNNING
        log(f"Telegram 频道已启动: @{self._bot_username}")

    async def stop(self) -> None:
        self._request_stop()
        async with self._lifecycle_lock:
            await self._stop_polling_thread()
            self._status = ChannelStatus.STOPPED
            log("Telegram 频道已停止")

    def _request_stop(self) -> None:
        """记录停止意图，初始化未完成时也不会丢失；取消在所属循环执行。"""
        self._stop_requested.set()
        loop = self._tg_loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._stop_in_loop)
            except RuntimeError:
                pass  # 线程已完成并关闭事件循环

    def _stop_in_loop(self) -> None:
        if self._stopping:
            return
        if self._started and self._stop_event is not None:
            self._stop_event.set()
        elif self._main_task is not None:
            self._main_task.cancel()

    async def _stop_polling_thread(self) -> None:
        """停掉独立轮询线程（幂等；join 超时不置空引用，避免重复 stop 误报）。"""
        self._request_stop()
        thread = self._thread
        if thread is not None:
            await asyncio.to_thread(thread.join, 15)
            if thread.is_alive():
                raise RuntimeError("Telegram 轮询线程退出超时，保留句柄并阻止重复启动")
        self._thread = None
        self._app = None
        self._requests = ()
        self._stop_event = None
        self._started = False

    # ------------------------------------------------------------------
    # 独立线程
    # ------------------------------------------------------------------

    def _run_thread(self) -> None:
        loop = asyncio.new_event_loop()
        self._tg_loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._main_task = loop.create_task(self._async_main())
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._start_error = self._format_startup_error(exc)
            self._status = ChannelStatus.ERROR
            log(f"Telegram 线程异常退出: {self._start_error}", "ERROR")
        finally:
            self._stopping = True
            self._ready.set()
            # 即使主协程在首次执行前被取消，也必须回收已创建的请求客户端。
            loop.run_until_complete(self._cleanup_application(self._app))
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._main_task = None
            self._tg_loop = None
            self._started = False
            self._ready.set()

    async def _async_main(self) -> None:
        app = self._require_app()
        self._stop_event = asyncio.Event()
        menu_task: Optional[asyncio.Task[None]] = None
        try:
            if self._stop_requested.is_set():
                raise asyncio.CancelledError
            self._start_stage = "初始化/getMe"
            await app.initialize()
            # initialize 已缓存 getMe 结果，避免启动时重复请求同一接口。
            self._bot_username = app.bot.username or ""
            self._bot_id = app.bot.id
            self._start_stage = "启动消息处理器"
            await app.start()
            self._start_stage = "启动轮询/deleteWebhook"
            await app.updater.start_polling(
                allowed_updates=[
                    "message", "edited_message", "channel_post",
                    "callback_query", "message_reaction",
                ],
                drop_pending_updates=True, bootstrap_retries=0,
            )
            self._started = True
            self._ready.set()
            from .commands import register_commands
            menu_task = asyncio.create_task(register_commands(app.bot))
            await self._stop_event.wait()
        except asyncio.CancelledError:
            if not self._started and not self._start_error:
                self._start_error = f"{self._start_stage}: 启动已取消"
            raise
        except Exception as exc:
            self._start_error = self._format_startup_error(exc)
            self._status = ChannelStatus.ERROR
            log(f"Telegram 启动异常: {self._start_error}", "ERROR", tag="通道")
        finally:
            self._stopping = True
            self._ready.set()
            if menu_task is not None:
                menu_task.cancel()
                await asyncio.gather(menu_task, return_exceptions=True)

    def _format_startup_error(self, exc: BaseException) -> str:
        from telegram.error import TimedOut

        token = self.config.bot_token
        detail = sanitize_text(str(exc).replace(token, "<redacted>") if token else str(exc))
        message = f"{self._start_stage}: {type(exc).__name__}: {detail}"
        if isinstance(exc, (TimedOut, TimeoutError)):
            message += "；请检查服务端代理与 api.telegram.org:443 连通性"
        return message

    async def _cleanup_application(self, app: Any) -> None:
        """在请求所属循环回收半初始化应用；单个步骤失败不阻断后续清理。"""
        callbacks: list[Callable[[], Awaitable[None]]] = []
        if app is not None:
            if app.updater is not None and app.updater.running:
                callbacks.append(app.updater.stop)
            if app.running:
                callbacks.append(app.stop)
            callbacks.append(app.shutdown)
        # PTB 的 application.shutdown 在 initialize 失败时可能提前返回。
        callbacks.extend(request.shutdown for request in self._requests)
        for callback in callbacks:
            try:
                await asyncio.wait_for(callback(), timeout=10)
            except Exception as exc:
                log(f"Telegram 清理异常: {self._format_startup_error(exc)}", "WARNING", tag="通道")

    # ------------------------------------------------------------------
    # 跨线程执行辅助
    # ------------------------------------------------------------------

    def _require_app(self) -> Any:
        """取得已启动的 Telegram 应用；停止后明确拒绝发送。"""
        if self._app is None:
            raise RuntimeError("Telegram 频道尚未启动或已停止")
        return self._app

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
                await tg_send.send_chat_action(self._require_app().bot, chat_id, "typing")
                return await deliver_reply(
                    self._require_app().bot, chat_id, text,
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
                    self._require_app().bot, chat_id, photo, caption=caption,
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
                return await tg_send.send_video(self._require_app().bot, chat_id, video, caption=caption)

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_audio(self, chat_id: str, audio: str, caption: str = "", **kwargs: Any) -> str:
        """通过 Telegram 发送音频。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.send_audio(self._require_app().bot, chat_id, audio, caption=caption)

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_voice(self, chat_id: str, voice: str, caption: str = "", **kwargs: Any) -> str:
        """通过 Telegram 发送语音消息。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.send_voice(self._require_app().bot, chat_id, voice)

            msg_id = await self._run_in_tg_loop(_do())
            return _ok({"message_id": msg_id, "chat_id": chat_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_file(self, chat_id: str, file_path: str, caption: str = "", **kwargs: Any) -> str:
        """通过 Telegram 发送文件。"""
        try:
            async def _do():
                from . import send as tg_send
                return await tg_send.send_file(self._require_app().bot, chat_id, file_path, caption=caption)

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
                    self._require_app().bot, chat_id, float(latitude), float(longitude),
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
                return await tg_send.send_animation(self._require_app().bot, chat_id, animation, caption=caption)

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
                    self._require_app().bot, chat_id, int(message_id), text,
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
                return await tg_send.delete_message(self._require_app().bot, chat_id, int(message_id))

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
                msg = await self._require_app().bot.send_contact(**params)
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
                msg = await self._require_app().bot.send_poll(chat_id=chat_id, question=question, options=opts)
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
                msg = await self._require_app().bot.forward_message(
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
                await self._require_app().bot.pin_chat_message(chat_id=chat_id, message_id=int(message_id))
            await self._run_in_tg_loop(_do())
            return _ok({"pinned": True, "chat_id": chat_id, "message_id": message_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def unpin_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """取消置顶 Telegram 群组中的消息。"""
        try:
            async def _do():
                await self._require_app().bot.unpin_chat_message(chat_id=chat_id, message_id=int(message_id))
            await self._run_in_tg_loop(_do())
            return _ok({"unpinned": True})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def get_chat_info(self, chat_id: str, **kwargs: Any) -> str:
        """查询 Telegram 会话详细信息（标题、类型、成员数等）。"""
        try:
            async def _do():
                chat = await self._require_app().bot.get_chat(chat_id=chat_id)
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
                        info["member_count"] = await self._require_app().bot.get_chat_member_count(chat_id)
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
                admins = await self._require_app().bot.get_chat_administrators(chat_id=chat_id)
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

    @channel_tool(sensitive=True)
    async def ban_user(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        """封禁 Telegram 群组中的用户。"""
        try:
            async def _do():
                await self._require_app().bot.ban_chat_member(chat_id=chat_id, user_id=int(user_id))
            await self._run_in_tg_loop(_do())
            return _ok({"banned": True, "chat_id": chat_id, "user_id": user_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def unban_user(self, chat_id: str, user_id: str, **kwargs: Any) -> str:
        """解除 Telegram 群组中用户的封禁。"""
        try:
            async def _do():
                await self._require_app().bot.unban_chat_member(chat_id=chat_id, user_id=int(user_id), only_if_banned=True)
            await self._run_in_tg_loop(_do())
            return _ok({"unbanned": True, "chat_id": chat_id, "user_id": user_id})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def set_chat_title(self, chat_id: str, title: str, **kwargs: Any) -> str:
        """修改 Telegram 群组标题。"""
        try:
            async def _do():
                await self._require_app().bot.set_chat_title(chat_id=chat_id, title=title)
            await self._run_in_tg_loop(_do())
            return _ok({"chat_id": chat_id, "title": title})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    @channel_tool()
    async def set_chat_description(self, chat_id: str, description: str, **kwargs: Any) -> str:
        """修改 Telegram 群组简介描述。"""
        try:
            async def _do():
                await self._require_app().bot.set_chat_description(chat_id=chat_id, description=description)
            await self._run_in_tg_loop(_do())
            return _ok({"chat_id": chat_id, "description": description[:50]})
        except Exception as exc:
            return _err(_fmt_exc(exc))

    # 已知会话容量上限（超出时按 LRU 淘汰最久未交互的会话）
    _KNOWN_CHATS_MAX = 1000

    def _record_chat(self, chat_id: str, chat_type: str, title: str = "", username: str = "") -> None:
        """记录已知会话（LRU：重复记录刷新热度，超容量淘汰最旧）。"""
        if chat_id in self._known_chats:
            self._known_chats.move_to_end(chat_id)
        self._known_chats[chat_id] = {
            "chat_id": chat_id,
            "type": chat_type,
            "title": title,
            "username": username,
        }
        while len(self._known_chats) > self._KNOWN_CHATS_MAX:
            self._known_chats.popitem(last=False)

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
            bot=self._require_app().bot if self._app else None,
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
        await handle_channel_post(
            update, context,
            on_message=self.on_message,
            channel_post_trigger=bool(self.config.channel_post_trigger),
        )


    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口（段分发模板见 BaseChannel._forward_via_segment_map）。"""
        return await self._forward_via_segment_map(request)

    async def get_self_info(self) -> ChannelUser:
        """获取 Bot 自身信息（经 _run_in_tg_loop 在 Telegram 事件循环中执行）。"""
        if not self._app or not self._require_app().bot:
            raise RuntimeError("Telegram 频道未初始化")
        bot_info = await self._run_in_tg_loop(self._require_app().bot.get_me())
        return ChannelUser(
            platform=self.channel_id,
            user_id=str(bot_info.id),
            user_name=bot_info.username or "",
            role=ChannelUserRole.MEMBER,
            is_bot=True,
        )

    async def get_user_info(self, user_id: str, channel_id: str) -> ChannelUser:
        """获取用户信息。"""
        if not self._app or not self._require_app().bot:
            raise RuntimeError("Telegram 频道未初始化")
        try:
            chat_id_int = int(channel_id.split("_", 1)[1]) if "_" in channel_id else int(channel_id)
            if channel_id.startswith("group") or chat_id_int < 0:
                member = await self._require_app().bot.get_chat_member(chat_id_int, int(user_id))
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
        if not self._app or not self._require_app().bot:
            raise RuntimeError("Telegram 频道未初始化")
        try:
            chat_id_int = int(channel_id.split("_", 1)[1]) if "_" in channel_id else int(channel_id)
            chat = await self._require_app().bot.get_chat(chat_id_int)
            chat_type = (
                ChannelType.PRIVATE
                if chat.type.value == "private"
                else ChannelType.GROUP
            )
            member_count: Optional[int] = None
            try:
                member_count = await self._require_app().bot.get_chat_member_count(chat_id_int)
            except Exception as exc:
                log(f"获取群成员数失败 ({channel_id}): {exc}", "DEBUG")
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
        """健康探针：调用 get_me 验证 Bot 可达（经 _run_in_tg_loop 跨线程执行）。"""
        if not self._app or not self._require_app().bot:
            return HealthStatus(
                healthy=False,
                detail="Telegram 频道未初始化",
                last_error="not_initialized",
            )
        try:
            started = time.time()
            await self._run_in_tg_loop(self._require_app().bot.get_me())
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

    async def render_approval_prompt(self, ctx: "ApprovalPromptRenderContext") -> SendRequest:
        """渲染批准提示（Telegram InlineKeyboard，覆盖基类纯文本默认实现）。"""

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
            segments=[SendSegment(type=SegmentType.TEXT, content=text)],
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
