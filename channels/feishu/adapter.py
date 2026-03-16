"""飞书频道 -- 基于 lark-oapi 的完整适配器。

继承 BaseChannel，声明完整能力集，支持 WebSocket 长连接和 Webhook 两种接入模式。
WebSocket 模式在独立线程中运行 lark.ws.Client，Webhook 模式启动 aiohttp HTTP 服务。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any, Dict, Optional, Set

import lark_oapi as lark

from core.log import log

from agent.core.channel.channel import BaseChannel, ChannelCapability, ChannelStatus, _ok, _err
from .config import FEISHU_CONFIGS
from .types import FeishuBotInfo

_AT_RE = re.compile(r'\[at_uid:([^\]]+)\]')


def _fmt_exc(exc: BaseException) -> str:
    msg = str(exc).strip()
    if not msg:
        return f"飞书频道错误（{type(exc).__name__}）"
    return msg


class FeishuChannel(BaseChannel):
    """飞书频道（支持 WebSocket 长连接 / Webhook 双模式）。"""

    _entity_description = "飞书频道"
    _adapter_configs = FEISHU_CONFIGS

    def __init__(self) -> None:
        self._client: Optional[lark.Client] = None
        self._ws_client: Optional[lark.ws.Client] = None
        self._bot_info: FeishuBotInfo = FeishuBotInfo()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._stop_event: Optional[asyncio.Event] = None
        self._start_error: str = ""
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._known_chats: Dict[str, Dict[str, Any]] = {}
        # Webhook 模式的 aiohttp runner
        self._webhook_runner: Optional[Any] = None
        super().__init__()

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def channel_id(self) -> str:
        return "feishu"

    @property
    def display_name(self) -> str:
        return "飞书"

    @property
    def capabilities(self) -> Set[ChannelCapability]:
        return {
            # 发送类
            ChannelCapability.SEND_TEXT,
            ChannelCapability.SEND_PHOTO,
            ChannelCapability.SEND_AUDIO,
            ChannelCapability.SEND_VIDEO,
            ChannelCapability.SEND_DOCUMENT,
            # 消息操作
            ChannelCapability.EDIT_MESSAGE,
            ChannelCapability.DELETE_MESSAGE,
            ChannelCapability.FORWARD_MESSAGE,
            ChannelCapability.PIN_MESSAGE,
            ChannelCapability.UNPIN_MESSAGE,
            # 信息查询
            ChannelCapability.GET_CHAT_INFO,
            ChannelCapability.GET_CHAT_MEMBERS,
            ChannelCapability.LIST_KNOWN_CHATS,
            # 高级
            ChannelCapability.REPLY_TO,
        }

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        online = self._status == ChannelStatus.RUNNING and bool(self._bot_info.open_id)
        info["online"] = online
        if self._bot_info.app_name:
            info["bot_name"] = self._bot_info.app_name
        if self._bot_info.open_id:
            info["bot_open_id"] = self._bot_info.open_id
        info["detail"] = (
            f"{self._bot_info.app_name} 在线" if online
            else ("连接失败" if self._start_error else "未连接")
        )
        if self._start_error:
            info["error"] = self._start_error
        return info

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        app_id: str = self.get_adapter_config("app_id", "")
        app_secret: str = self.get_adapter_config("app_secret", "")
        if not app_id or not app_secret:
            log("飞书 App ID / App Secret 未配置，频道无法启动", "WARNING")
            self._status = ChannelStatus.ERROR
            return

        domain_str: str = self.get_adapter_config("domain", "feishu")
        domain = lark.FEISHU_DOMAIN if domain_str == "feishu" else lark.LARK_DOMAIN

        # 创建 API Client
        self._client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .domain(domain) \
            .log_level(lark.LogLevel.WARNING) \
            .build()

        # 获取 Bot 身份
        try:
            from .send import get_bot_info
            bot_data = await get_bot_info(self._client)
            self._bot_info = FeishuBotInfo(
                open_id=bot_data.get("open_id", ""),
                app_name=bot_data.get("app_name", ""),
            )
            log(f"飞书 Bot: {self._bot_info.app_name} ({self._bot_info.open_id})")
        except Exception as exc:
            self._start_error = f"获取 Bot 信息失败: {exc}"
            self._status = ChannelStatus.ERROR
            log(f"飞书: {self._start_error}", "ERROR")
            return

        # 记住主事件循环以便 WS 线程回调
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._main_loop = None

        # 选择接入模式
        mode: str = self.get_adapter_config("connection_mode", "websocket")
        if mode == "webhook":
            await self._start_webhook(app_id, app_secret, domain_str)
        else:
            self._start_websocket(app_id, app_secret)

    async def stop(self) -> None:
        self._status = ChannelStatus.STOPPED
        # 停止 WebSocket 客户端
        if self._ws_client:
            self._ws_client = None
        # 停止 Webhook 服务
        if self._webhook_runner:
            try:
                await self._webhook_runner.cleanup()
            except Exception:
                pass
            self._webhook_runner = None
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        self._client = None
        log("飞书频道已停止")

    # ------------------------------------------------------------------
    # WebSocket 长连接模式
    # ------------------------------------------------------------------

    @staticmethod
    def _setup_sdk_logger() -> None:
        """将 lark SDK 的内部日志桥接到我们的 log() 系统，以便捕获 WS 连接和事件处理错误。"""
        from lark_oapi.core.log import logger as _sdk_logger

        if any(isinstance(h, _LarkSdkLogHandler) for h in _sdk_logger.handlers):
            return
        handler = _LarkSdkLogHandler()
        handler.setLevel(logging.DEBUG)
        _sdk_logger.addHandler(handler)
        _sdk_logger.setLevel(logging.DEBUG)

    def _start_websocket(self, app_id: str, app_secret: str) -> None:
        """在独立线程中启动 lark.ws.Client 长连接。"""
        require_mention = bool(self.get_adapter_config("require_mention", True))

        from .handlers import build_message_handler

        msg_handler = build_message_handler(
            client=self._client,  # type: ignore[arg-type]
            bot_open_id=self._bot_info.open_id,
            require_mention=require_mention,
            on_message=self.on_message,
            main_loop=self._main_loop,
        )

        event_handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(msg_handler) \
            .build()

        # 对未注册的事件类型静默处理，避免 SDK 抛 EventException 产生 ERROR 日志
        _orig_dispatch = event_handler.do_without_validation

        def _safe_dispatch(payload: bytes) -> object:
            try:
                return _orig_dispatch(payload)
            except Exception as exc:
                if "processor not found" in str(exc):
                    log(f"飞书: 忽略未订阅的事件类型 ({exc})", "DEBUG")
                    return None
                raise

        event_handler.do_without_validation = _safe_dispatch  # type: ignore[method-assign]

        self._ws_client = lark.ws.Client(
            app_id, app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.DEBUG,
        )
        # Client.__init__ 会将 SDK logger 级别设为指定 level，必须在其之后调用
        self._setup_sdk_logger()

        self._ready.clear()
        self._start_error = ""
        self._thread = threading.Thread(
            target=self._run_ws_thread, daemon=True, name="feishu-ws",
        )
        self._thread.start()

        if not self._ready.wait(timeout=15):
            err = self._start_error or "WebSocket 启动超时"
            self._status = ChannelStatus.ERROR
            raise RuntimeError(f"飞书频道启动失败: {err}")

        if self._start_error:
            self._status = ChannelStatus.ERROR
            raise RuntimeError(f"飞书频道启动失败: {self._start_error}")

        self._status = ChannelStatus.RUNNING
        log(f"飞书频道已启动 (WebSocket): {self._bot_info.app_name}")

    def _run_ws_thread(self) -> None:
        """WS 线程入口。

        lark SDK 在模块加载时将当时的事件循环保存为模块级变量 `loop`，
        Client.start() 硬编码使用该变量，导致无法直接在子线程中运行。
        解决方案：创建新 loop 后覆盖 SDK 的模块级变量，并重建 Lock。
        """
        import lark_oapi.ws.client as _lark_ws_module

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _lark_ws_module.loop = loop  # 重定向 SDK 模块级 loop 到本线程 loop
        if self._ws_client:
            self._ws_client._lock = asyncio.Lock()  # Lock 需绑定当前 loop
        def _check_connection_status() -> None:
            conn = getattr(self._ws_client, "_conn", None)
            if conn is not None:
                conn_id = getattr(self._ws_client, "_conn_id", "")
                log(f"飞书 WS 连接确认：已建立 conn_id={conn_id}", "DEBUG")
            else:
                log("飞书 WS 连接确认：_conn 为 None，连接尚未建立或已断开", "WARNING")

        try:
            self._ready.set()
            if self._ws_client:
                log("飞书 WS 线程：开始连接...", "DEBUG")
                # 3 秒后检查连接状态（start() 内部先发 HTTP 请求再建 WS）
                loop.call_later(3, _check_connection_status)
                self._ws_client.start()
                log("飞书 WS 线程：start() 正常退出", "DEBUG")
        except Exception as exc:
            self._start_error = str(exc)
            self._ready.set()
            log(f"飞书 WebSocket 异常退出: {exc}", "ERROR")
        finally:
            loop.close()

    # ------------------------------------------------------------------
    # Webhook 模式
    # ------------------------------------------------------------------

    async def _start_webhook(self, app_id: str, app_secret: str, domain_str: str) -> None:
        """启动 aiohttp HTTP 服务监听飞书 Webhook 回调。"""
        from aiohttp import web

        encrypt_key: str = self.get_adapter_config("encrypt_key", "")
        verification_token: str = self.get_adapter_config("verification_token", "")
        port: int = int(self.get_adapter_config("webhook_port", 9321))
        require_mention = bool(self.get_adapter_config("require_mention", True))

        from .handlers import build_message_handler

        msg_handler = build_message_handler(
            client=self._client,  # type: ignore[arg-type]
            bot_open_id=self._bot_info.open_id,
            require_mention=require_mention,
            on_message=self.on_message,
        )

        event_handler = lark.EventDispatcherHandler.builder(
            encrypt_key, verification_token
        ).register_p2_im_message_receive_v1(msg_handler).build()

        async def _webhook_handler(request: web.Request) -> web.Response:
            body = await request.read()
            headers: Dict[str, str] = {}
            for k, v in request.headers.items():
                headers[k] = v
            http_req = lark.RawRequest()
            http_req.body = body
            http_req.headers = headers
            resp = event_handler.do(http_req)
            return web.Response(
                body=resp.body if hasattr(resp, "body") else b"",
                status=resp.status_code if hasattr(resp, "status_code") else 200,
                content_type="application/json",
            )

        app = web.Application()
        app.router.add_post("/feishu/webhook", _webhook_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        self._webhook_runner = runner

        self._status = ChannelStatus.RUNNING
        log(f"飞书频道已启动 (Webhook): {self._bot_info.app_name}, 端口 {port}")

    # ------------------------------------------------------------------
    # 能力方法
    # ------------------------------------------------------------------

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """发送文本消息。"""
        if not self._client:
            return _err("飞书频道未就绪")
        reply_to = kwargs.get("reply_to")
        text_limit = int(self.get_adapter_config("text_limit", 4000))
        text = self._convert_at_to_lark(text)
        try:
            from . import send as feishu_send
            result = await feishu_send.send_text(
                self._client, chat_id, text,
                reply_to=reply_to,
                text_limit=text_limit,
            )
            return _ok(result)
        except Exception as exc:
            log(f"飞书 send_text 失败: {exc}", "ERROR")
            return _err(_fmt_exc(exc))

    @staticmethod
    def _convert_at_to_lark(text: str) -> str:
        """将 [at_uid:xxx] 转为飞书 <at> 格式。"""
        def _replacer(m: re.Match[str]) -> str:
            uid = m.group(1)
            if uid == "all":
                return '<at user_id="all">全体成员</at>'
            return f'<at user_id="{uid}">{uid}</at>'
        return _AT_RE.sub(_replacer, text)

    async def send_photo(self, chat_id: str, photo: str, caption: str = "", **kwargs: Any) -> str:
        """发送图片。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.send_photo(
                self._client, chat_id, photo,
                caption=caption,
                reply_to=kwargs.get("reply_to"),
            )
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_audio(self, chat_id: str, audio: str, caption: str = "", **kwargs: Any) -> str:
        """发送音频文件。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.send_audio(
                self._client, chat_id, audio,
                caption=caption,
                reply_to=kwargs.get("reply_to"),
            )
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_video(self, chat_id: str, video: str, caption: str = "", **kwargs: Any) -> str:
        """发送视频。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.send_video(
                self._client, chat_id, video,
                caption=caption,
                reply_to=kwargs.get("reply_to"),
            )
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def send_document(self, chat_id: str, document: str, caption: str = "", **kwargs: Any) -> str:
        """发送文件。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.send_file(
                self._client, chat_id, document,
                caption=caption,
                reply_to=kwargs.get("reply_to"),
            )
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def edit_message(self, chat_id: str, message_id: str, text: str, **kwargs: Any) -> str:
        """编辑已发送的消息。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.edit_message(self._client, message_id, text)
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def delete_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """删除消息。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.delete_message(self._client, message_id)
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def forward_message(self, chat_id: str, from_chat_id: str, message_id: str, **kwargs: Any) -> str:
        """转发消息到另一个会话。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.forward_message(self._client, message_id, chat_id)
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def pin_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """置顶消息。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.pin_message(self._client, message_id)
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def unpin_message(self, chat_id: str, message_id: str, **kwargs: Any) -> str:
        """取消置顶消息。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.unpin_message(self._client, message_id)
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def get_chat_info(self, chat_id: str, **kwargs: Any) -> str:
        """查询会话详细信息。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.get_chat_info(self._client, chat_id)
            # 记录已知会话
            name = result.get("name", "")
            self._known_chats[chat_id] = {
                "chat_id": chat_id,
                "name": name,
                "type": result.get("chat_type", ""),
            }
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def get_chat_members(self, chat_id: str, **kwargs: Any) -> str:
        """查询会话成员列表。"""
        if not self._client:
            return _err("飞书频道未就绪")
        try:
            from . import send as feishu_send
            result = await feishu_send.get_chat_members(self._client, chat_id)
            return _ok(result)
        except Exception as exc:
            return _err(_fmt_exc(exc))

    async def list_known_chats(self, **kwargs: Any) -> str:
        """列出已知会话。"""
        if not self._known_chats:
            return _ok({
                "chats": [],
                "hint": "Bot 尚未与任何会话交互。当有人发消息给 Bot 后，会话信息会自动记录。",
            })
        return _ok({"chats": list(self._known_chats.values()), "count": len(self._known_chats)})

    # ------------------------------------------------------------------
    # 向后兼容
    # ------------------------------------------------------------------

    async def send_message(self, request: Any) -> bool:
        """兼容旧的 AdapterSendRequest 接口。"""
        chat_id = request.channel.channel_id
        text = request.content
        reply_to = getattr(request, "reply_to", None)
        result_json = await self.send_text(chat_id, text, reply_to=reply_to)
        result = json.loads(result_json)
        return result.get("success", False)


class _LarkSdkLogHandler(logging.Handler):
    """将 lark-oapi SDK 的内部日志转发到项目统一日志系统。"""

    _LEVEL_MAP: Dict[int, str] = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "ERROR",
    }

    def emit(self, record: logging.LogRecord) -> None:
        level = self._LEVEL_MAP.get(record.levelno, "DEBUG")
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        log(f"[lark-sdk] {msg}", level)


CHANNEL_CLASS = FeishuChannel
