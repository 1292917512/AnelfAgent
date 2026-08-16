"""NoneBot worker 主入口 — 在独立 venv 中运行完整 NoneBot 客户端并桥接到主进程。

启动链（父进程 NoneBotRuntime 以本脚本 spawn，cwd 为运行时目录）：
1. 读取 config.json（适配器注册表条目 / 插件列表 / intercept_all）；
2. ``nonebot.init()``（读取运行时目录 .env：DRIVER/HOST/PORT/各平台凭据）；
3. 注册适配器、加载插件、安装桥接钩子；
4. ``nonebot.run()`` 启动 driver，桥接客户端随 driver 启动回连主进程。

能力：
- 平台事件经 ``event_preprocessor`` 转线协议上报父进程（intercept_all 时拦截插件处理）；
- 处理父进程下行的发送请求（OneBot v11/v12 精确 + Telegram/常规适配器尽力而为）；
- ``run_command``：合成事件触发任意插件命令并捕获回复（Bot.send 截获）。

仅在 worker venv 中以脚本方式运行（裸导入同目录模块）。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

sys.path.insert(0, str(Path(__file__).resolve().parent))

import nonebot
from bridge_client import BridgeClient, bridge_env
from nonebot.adapters import Bot, Event
from nonebot.exception import IgnoredException
from nonebot.message import event_preprocessor
from nonebot.message import handle_event as nb_handle_event
from protocol import CMD_GET_PLUGINS, CMD_GET_STATUS, CMD_RUN_COMMAND, WIRE_VERSION
from wire_out import convert_event_to_wire

RUNTIME_DIR = Path.cwd()
CONFIG_PATH = RUNTIME_DIR / "config.json"

# Adapter 类 → 注册表 key（注册适配器时构建）
adapter_keys: Dict[Type[Any], str] = {}
registered_adapter_keys: List[str] = []
client: Optional[BridgeClient] = None

# 合成命令执行期间的 Bot.send 截获缓冲
_capture_buffer: ContextVar[Optional[List[str]]] = ContextVar("nb_capture", default=None)

_COMMAND_TIMEOUT = 60.0


# ------------------------------------------------------------------
# Bot.send 截获（合成命令期间捕获插件回复，不真正发到平台）
# ------------------------------------------------------------------

_orig_bot_send = Bot.send


async def _capturing_send(self: Any, event: Any, message: Any, **kwargs: Any) -> Any:
    buffer = _capture_buffer.get()
    if buffer is not None:
        buffer.append(str(message))
        return None
    return await _orig_bot_send(self, event, message, **kwargs)


Bot.send = _capturing_send


# ------------------------------------------------------------------
# 状态快照
# ------------------------------------------------------------------


def build_status_payload() -> Dict[str, Any]:
    """构造 worker 状态快照（bots / 适配器 / 插件）。"""
    bots: List[Dict[str, str]] = []
    for bot_id, bot in nonebot.get_bots().items():
        adapter = getattr(bot, "adapter", None)
        key = adapter_keys.get(type(adapter)) if adapter is not None else None
        bots.append({"bot_id": str(bot_id), "adapter": key or "unknown"})

    plugins: List[Dict[str, Any]] = []
    try:
        loaded = nonebot.get_loaded_plugins()
    except Exception:  # noqa: BLE001 - 插件枚举失败不影响状态
        loaded = set()
    for plugin in loaded:
        meta = plugin.metadata
        plugins.append({
            "module": getattr(plugin, "module_name", "") or "",
            "name": getattr(meta, "name", "") if meta else "",
            "description": getattr(meta, "description", "") if meta else "",
            "usage": getattr(meta, "usage", "") if meta else "",
            "type": getattr(meta, "type", None) if meta else None,
            "homepage": getattr(meta, "homepage", None) if meta else None,
            "supported_adapters": (
                sorted(getattr(meta, "supported_adapters", None) or [])
                if meta and getattr(meta, "supported_adapters", None)
                else None
            ),
            "matcher_count": len(getattr(plugin, "matcher", None) or set()),
        })
    plugins.sort(key=lambda p: p["module"])

    return {
        "wire_version": WIRE_VERSION,
        "nonebot_version": getattr(nonebot, "__version__", ""),
        "adapters": list(registered_adapter_keys),
        "bots": bots,
        "plugins": plugins,
    }


# ------------------------------------------------------------------
# 发送请求处理（父进程 MSG_SEND）
# ------------------------------------------------------------------


def _resolve_bot(bot_id: str, adapter_key: str) -> Optional[Any]:
    """按 bot_id > 适配器 key > 首个在线 解析 Bot。"""
    bots = nonebot.get_bots()
    if not bots:
        return None
    if bot_id and bot_id in bots:
        return bots[bot_id]
    if adapter_key:
        for bot in bots.values():
            adapter = getattr(bot, "adapter", None)
            if adapter is not None and adapter_keys.get(type(adapter)) == adapter_key:
                return bot
    return next(iter(bots.values()))


def _bot_adapter_key(bot: Any) -> str:
    adapter = getattr(bot, "adapter", None)
    if adapter is None:
        return ""
    return adapter_keys.get(type(adapter)) or ""


async def handle_send(payload: Dict[str, Any]) -> Dict[str, Any]:
    """处理父进程下行的发送请求。"""
    bot = _resolve_bot(str(payload.get("bot_id", "")), str(payload.get("adapter", "")))
    if bot is None:
        return {"ok": False, "error": "没有在线的 Bot"}

    target = str(payload.get("target", ""))
    channel_type = str(payload.get("channel_type", "private"))
    text = str(payload.get("text", ""))
    reply_to = str(payload.get("reply_to", "") or "")
    image = str(payload.get("image", "") or "")

    key = _bot_adapter_key(bot)
    try:
        if key == "onebot_v11":
            message_id = await _send_onebot_v11(bot, target, channel_type, text, reply_to, image)
        elif key == "onebot_v12":
            message_id = await _send_onebot_v12(bot, target, channel_type, text, reply_to, image)
        elif key == "telegram":
            message_id = await _send_telegram(bot, target, text, reply_to, image)
        elif key == "discord":
            await bot.call_api("create_message", channel_id=int(target), content=text)
            message_id = ""
        else:
            message_id = await _send_generic(bot, target, channel_type, text)
        return {"ok": True, "message_id": message_id}
    except Exception as exc:  # noqa: BLE001 - 发送异常如实回传父进程
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _send_onebot_v11(
    bot: Any, target: str, channel_type: str, text: str, reply_to: str, image: str
) -> str:
    """OneBot V11 精确发送（支持图片与回复引用）。"""
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    segments: List[Any] = []
    if reply_to:
        segments.append(MessageSegment.reply(reply_to))
    if image:
        segments.append(MessageSegment.image(image))
    if text:
        segments.append(MessageSegment.text(text))
    message = Message(segments)

    if channel_type == "group":
        result = await bot.call_api("send_group_msg", group_id=int(target), message=message)
    else:
        result = await bot.call_api(
            "send_msg", message_type="private", user_id=int(target), message=message
        )
    return str((result or {}).get("message_id", ""))


async def _send_onebot_v12(
    bot: Any, target: str, channel_type: str, text: str, reply_to: str, image: str
) -> str:
    """OneBot V12 统一 send_message 发送。"""
    from nonebot.adapters.onebot.v12 import Message, MessageSegment

    segments: List[Any] = []
    if image:
        segments.append(MessageSegment.image(image))
    if text:
        segments.append(MessageSegment.text(text))
    params: Dict[str, Any] = {"message": Message(segments), "detail_type": channel_type}
    if channel_type == "group":
        params["group_id"] = target
    else:
        params["user_id"] = target
    result = await bot.call_api("send_message", **params)
    return str((result or {}).get("message_id", ""))


async def _send_telegram(bot: Any, target: str, text: str, reply_to: str, image: str) -> str:
    """Telegram 发送（图片 URL 直传，本地文件读入字节）。"""
    chat_id: Any = target
    try:
        chat_id = int(target)
    except ValueError:
        pass

    if image:
        photo: Any = image
        if not image.startswith(("http://", "https://")):
            photo = Path(image).read_bytes()
        await bot.call_api("sendPhoto", chat_id=chat_id, photo=photo)

    params: Dict[str, Any] = {"chat_id": chat_id, "text": text or " "}
    if reply_to:
        try:
            params["reply_to_message_id"] = int(reply_to)
        except ValueError:
            pass
    result = await bot.call_api("sendMessage", **params)
    return str((result or {}).get("message_id", ""))


async def _send_generic(bot: Any, target: str, channel_type: str, text: str) -> str:
    """通用适配器尽力而为发送：依次尝试常见 API 参数形态。"""
    attempts: List[Dict[str, Any]] = [
        {"target": target, "message": text},
        {"chat_id": target, "text": text},
    ]
    last_error: Optional[Exception] = None
    for params in attempts:
        try:
            await bot.call_api("send_message", **params)
            return ""
        except Exception as exc:  # noqa: BLE001 - 逐个参数形态尝试
            last_error = exc
    raise RuntimeError(f"通用发送失败: {last_error}")


# ------------------------------------------------------------------
# 控制命令处理（父进程 MSG_CMD）
# ------------------------------------------------------------------


async def handle_command(payload: Dict[str, Any]) -> Dict[str, Any]:
    """处理父进程控制命令。"""
    action = payload.get("action", "")

    if action in (CMD_GET_STATUS, CMD_GET_PLUGINS):
        return {"ok": True, "status": build_status_payload()}

    if action == CMD_RUN_COMMAND:
        return await _run_command(payload)

    return {"ok": False, "error": f"未知命令: {action}"}


async def _run_command(payload: Dict[str, Any]) -> Dict[str, Any]:
    """合成事件触发插件命令并捕获回复。

    以虚拟用户（AnelfAgent）身份构造私聊消息事件，经 NoneBot 官方分发入口
    ``nonebot.message.handle_event`` 走完整匹配管线；期间 ``Bot.send`` 被
    截获到缓冲区（不真正发送到平台），结束后回传给 AI。
    """
    command = str(payload.get("command", "")).strip()
    if not command:
        return {"ok": False, "error": "命令内容为空"}

    bot = _resolve_bot(str(payload.get("bot_id", "")), str(payload.get("adapter", "")))
    if bot is None:
        return {"ok": False, "error": "没有在线的 Bot"}

    key = _bot_adapter_key(bot)
    event = _fabricate_command_event(key, bot, command)
    if event is None:
        return {"ok": False, "error": f"适配器 {key} 暂不支持 AI 命令触发"}

    buffer: List[str] = []
    token = _capture_buffer.set(buffer)
    try:
        await asyncio.wait_for(nb_handle_event(bot, event), timeout=_COMMAND_TIMEOUT)
    except asyncio.TimeoutError:
        return {"ok": True, "replies": buffer, "timeout": True}
    except Exception as exc:  # noqa: BLE001 - 命令执行异常如实回传
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        _capture_buffer.reset(token)

    replies = [line for line in buffer if line.strip()]
    return {"ok": True, "replies": replies}


def _fabricate_command_event(key: str, bot: Any, command: str) -> Optional[Any]:
    """按适配器构造合成私聊命令事件（目前支持 OneBot V11）。"""
    if key == "onebot_v11":
        from nonebot.adapters.onebot.v11 import Message, PrivateMessageEvent
        from nonebot.adapters.onebot.v11.event import Sender

        message = Message(command)
        return PrivateMessageEvent(
            time=int(time.time()),
            self_id=int(bot.self_id),
            post_type="message",
            sub_type="friend",
            user_id=10000,
            message=message,
            original_message=message.copy(),
            raw_message=command,
            font=0,
            sender=Sender(user_id=10000, nickname="AnelfAgent"),
            to_me=True,
        )
    return None


# ------------------------------------------------------------------
# 主入口
# ------------------------------------------------------------------


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> None:
    global client

    cfg = load_config()
    ws_url, token = bridge_env()

    nonebot.init()
    driver = nonebot.get_driver()

    # 注册适配器（config.json 条目：key/import/class）
    for entry in cfg.get("adapters") or []:
        try:
            module = importlib.import_module(entry["import"])
            adapter_cls = getattr(module, entry.get("class", "Adapter"))
            driver.register_adapter(adapter_cls)
            adapter_keys[adapter_cls] = entry["key"]
            registered_adapter_keys.append(entry["key"])
            print(f"[bridge] 适配器已注册: {entry['key']}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - 单个适配器失败不阻塞其余
            print(
                f"[bridge] 适配器加载失败 {entry.get('key')}: {exc}\n"
                f"  若因未安装，请先在 Web 界面安装适配器包",
                file=sys.stderr,
            )

    # 加载插件
    for module_name in cfg.get("plugins") or []:
        try:
            plugin = nonebot.load_plugin(module_name)
            print(
                f"[bridge] 插件已加载: {module_name}"
                + ("" if plugin else "（返回 None）"),
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001 - 单个插件失败不阻塞其余
            print(f"[bridge] 插件加载失败 {module_name}: {exc}", file=sys.stderr)

    # 桥接客户端
    client = BridgeClient(
        ws_url,
        token,
        on_send=handle_send,
        on_command=handle_command,
        status_provider=build_status_payload,
    )

    intercept_all = bool(cfg.get("intercept_all", False))

    @event_preprocessor
    async def _bridge_preprocessor(bot: Bot, event: Event) -> None:
        """平台事件 → 线协议上报父进程；intercept_all 时拦截插件处理。

        注意：NoneBot 依赖注入按参数类型解析，钩子签名必须使用 Bot/Event 注解。
        """
        if client is None:
            return
        wire = convert_event_to_wire(bot, event, adapter_keys)
        if wire is None:
            return
        await client.send_event(wire)
        if intercept_all:
            raise IgnoredException("Handled by AnelfAgent NoneBot Bridge")

    @driver.on_bot_connect
    async def _on_bot_connect(bot: Bot) -> None:
        if client is not None:
            await client.push_status()

    @driver.on_bot_disconnect
    async def _on_bot_disconnect(bot: Bot) -> None:
        if client is not None:
            await client.push_status()

    driver.on_startup(client.start)
    driver.on_shutdown(client.stop)

    nonebot.run()


if __name__ == "__main__":
    main()
