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
from segments import (
    file_display_name,
    plain_at_text,
    resolve_media_source,
    split_at_segments,
)
from wire_out import (
    convert_event_to_wire,
    extract_message_text,
    get_adapter_key,
)

RUNTIME_DIR = Path.cwd()
CONFIG_PATH = RUNTIME_DIR / "config.json"

# Adapter 类 → 注册表 key（注册适配器时构建）
adapter_keys: Dict[Type[Any], str] = {}
registered_adapter_keys: List[str] = []
client: Optional[BridgeClient] = None

# 合成命令执行期间的 Bot.send 截获缓冲
_capture_buffer: ContextVar[Optional[List[str]]] = ContextVar("nb_capture", default=None)

_COMMAND_TIMEOUT = 60.0
# 事件增强预算：单查询超时与整体并发窗口（秒）；最多增强前 N 张图
_ENRICH_BUDGET = 5.0
_ENRICH_MAX_IMAGES = 4


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


async def _enrich_onebot_event(bot: Any, wire: Dict[str, Any]) -> None:
    """OneBot v11 事件深度增强（best-effort，运行在事件预处理器内）。

    1. 回复引用回捞：get_msg 取被回复消息的文本 → ``reply_content``；
    2. NapCat 本地图片零拷贝：to_me 消息的图片段经 get_image 解析出
       协议端本地路径 → 段 ``local`` 字段（父进程直接用作本地文件，免下载）。

    全部查询并发执行且带总预算（_ENRICH_BUDGET），多图消息不再逐张串行
    阻塞事件管线；超预算直接放弃增强，走 URL 下载兜底。
    """
    image_segs = [
        seg for seg in (wire.get("segments") or [])
        if isinstance(seg, dict) and seg.get("seg") == "image" and seg.get("file")
    ][:_ENRICH_MAX_IMAGES]

    async def _fetch_reply() -> None:
        reply_to = str(wire.get("reply_to", "") or "")
        if not reply_to or wire.get("reply_content"):
            return
        data = await asyncio.wait_for(
            bot.call_api("get_msg", message_id=int(reply_to)),
            timeout=_ENRICH_BUDGET,
        )
        wire["reply_content"] = extract_message_text(data)[:500]

    async def _fetch_local(seg: Dict[str, Any]) -> None:
        data = await asyncio.wait_for(
            bot.call_api("get_image", file=str(seg.get("file", ""))),
            timeout=_ENRICH_BUDGET,
        )
        local = str((data or {}).get("file", "") or "")
        if local and Path(local).is_absolute():
            seg["local"] = local

    tasks: List[Any] = []
    if wire.get("reply_to"):
        tasks.append(_fetch_reply())
    if wire.get("is_to_me"):
        tasks.extend(_fetch_local(seg) for seg in image_segs)
    if not tasks:
        return

    try:
        # gather 并发 + 整体预算；单任务失败不影响其余（URL 下载兜底）
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_ENRICH_BUDGET + 1.0,
        )
    except asyncio.TimeoutError:
        pass


# ------------------------------------------------------------------
# 出站发送器注册表 —— 新增平台支持 = 实现一个 sender + 注册一行
# ------------------------------------------------------------------


class SendContext:
    """单次发送请求的上下文（bot / 目标 / 文本 / 媒体）。"""

    __slots__ = ("bot", "adapter_key", "target", "channel_type", "text",
                 "reply_to", "media_kind", "media_source", "media_name")

    def __init__(self, payload: Dict[str, Any], bot: Any, adapter_key: str) -> None:
        media = payload.get("media") or {}
        self.bot = bot
        self.adapter_key = adapter_key
        self.target = str(payload.get("target", ""))
        self.channel_type = str(payload.get("channel_type", "private"))
        self.text = str(payload.get("text", ""))
        self.reply_to = str(payload.get("reply_to", "") or "")
        self.media_kind = str(media.get("kind", "") or "")
        self.media_source = str(media.get("source", "") or "")
        self.media_name = str(media.get("name", "") or "")


# 适配器 key → sender（ctx → message_id）；未注册的适配器走 _send_generic
_SENDERS: Dict[str, Any] = {}


def _sender(adapter_key: str):
    """注册发送器装饰器。"""
    def decorator(func: Any) -> Any:
        _SENDERS[adapter_key] = func
        return func
    return decorator


async def handle_send(payload: Dict[str, Any]) -> Dict[str, Any]:
    """处理父进程下行的发送请求。

    media 载荷：``{"kind": "image|voice|video|file", "source": 路径或URL或file_id, "name": 覆盖名}``
    按适配器 key 查 ``_SENDERS`` 注册表分发；新增平台无需改动本函数。
    """
    bot = _resolve_bot(str(payload.get("bot_id", "")), str(payload.get("adapter", "")))
    if bot is None:
        return {"ok": False, "error": "没有在线的 Bot"}

    ctx = SendContext(payload, bot, _bot_adapter_key(bot))
    sender = _SENDERS.get(ctx.adapter_key, _send_generic_ctx)
    try:
        message_id = await sender(ctx)
        return {"ok": True, "message_id": message_id}
    except Exception as exc:  # noqa: BLE001 - 发送异常如实回传父进程
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _send_generic_ctx(ctx: "SendContext") -> str:
    """通用兜底发送（未注册专属 sender 的适配器）。"""
    return await _send_generic(ctx.bot, ctx.target, ctx.channel_type, ctx.text)


@_sender("discord")
async def _send_discord(ctx: "SendContext") -> str:
    """Discord 文本发送（媒体暂不支持，错误如实抛出）。"""
    if ctx.media_kind:
        raise RuntimeError("Discord 媒体发送暂未实现，请经 nonebot 插件生态处理")
    await ctx.bot.call_api(
        "create_message", channel_id=int(ctx.target), content=plain_at_text(ctx.text)
    )
    return ""


async def _resolve_source(source: str) -> str:
    """媒体源解析（本地文件读取放线程，避免大文件阻塞事件循环）。"""
    if not source:
        return ""
    return await asyncio.to_thread(resolve_media_source, source)


@_sender("onebot_v11")
async def _send_onebot_v11(ctx: "SendContext") -> str:
    """OneBot V11 精确发送：at 段 / 回复引用 / 图片 / 语音 / 视频 / 文件。

    - 文本中的 [at_uid:x] 转为真正的 at 消息段（@全体成员）；
    - 图片/语音/视频本地文件 base64 内联（URL/file_id 直传）；
    - 文件走 upload_group_file/upload_private_file（群文件/私聊文件 API）。
    """
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    bot, target = ctx.bot, ctx.target

    # 文件类：独立上传 API，不进消息段
    if ctx.media_kind == "file" and ctx.media_source:
        name = file_display_name(ctx.media_source, ctx.media_name)
        if ctx.channel_type == "group":
            await bot.call_api(
                "upload_group_file", group_id=int(target), file=ctx.media_source, name=name
            )
        else:
            await bot.call_api(
                "upload_private_file", user_id=int(target), file=ctx.media_source, name=name
            )
        return ""

    segments: List[Any] = []
    if ctx.reply_to:
        segments.append(MessageSegment.reply(ctx.reply_to))

    if ctx.media_kind in ("image", "voice", "video") and ctx.media_source:
        source = await _resolve_source(ctx.media_source)
        if ctx.media_kind == "image":
            segments.append(MessageSegment.image(source))
        elif ctx.media_kind == "voice":
            segments.append(MessageSegment.record(source))
        else:
            segments.append(MessageSegment.video(source))

    # 文本 → at/text 有序段
    for seg_kind, value in split_at_segments(ctx.text):
        if seg_kind == "at":
            segments.append(
                MessageSegment.at("all" if value == "all" else int(value))
            )
        elif value:
            segments.append(MessageSegment.text(value))

    message = Message(segments)
    if ctx.channel_type == "group":
        result = await bot.call_api("send_group_msg", group_id=int(target), message=message)
    else:
        result = await bot.call_api(
            "send_msg", message_type="private", user_id=int(target), message=message
        )
    return str((result or {}).get("message_id", ""))


@_sender("onebot_v12")
async def _send_onebot_v12(ctx: "SendContext") -> str:
    """OneBot V12 统一 send_message 发送（媒体图片/语音/视频内联，at 文本化）。"""
    from nonebot.adapters.onebot.v12 import Message, MessageSegment

    segments: List[Any] = []
    if ctx.media_kind in ("image", "voice", "video") and ctx.media_source:
        source = await _resolve_source(ctx.media_source)
        if ctx.media_kind == "image":
            segments.append(MessageSegment.image(source))
        elif ctx.media_kind == "voice":
            segments.append(MessageSegment.audio(source))
        else:
            segments.append(MessageSegment.video(source))
    plain = plain_at_text(ctx.text)
    if plain:
        segments.append(MessageSegment.text(plain))
    params: Dict[str, Any] = {"message": Message(segments), "detail_type": ctx.channel_type}
    if ctx.channel_type == "group":
        params["group_id"] = ctx.target
    else:
        params["user_id"] = ctx.target
    result = await ctx.bot.call_api("send_message", **params)
    return str((result or {}).get("message_id", ""))


@_sender("telegram")
async def _send_telegram(ctx: "SendContext") -> str:
    """Telegram 发送：图片/语音/视频/文档 + 文本（URL 直传，本地读字节）。"""
    chat_id: Any = ctx.target
    try:
        chat_id = int(ctx.target)
    except ValueError:
        pass

    if ctx.media_kind and ctx.media_source:
        payload: Any = ctx.media_source
        if not ctx.media_source.startswith(("http://", "https://")):
            payload = await asyncio.to_thread(Path(ctx.media_source).read_bytes)
        api = {
            "image": ("sendPhoto", {"photo": payload}),
            "voice": ("sendVoice", {"voice": payload}),
            "video": ("sendVideo", {"video": payload}),
            "file": ("sendDocument", {"document": payload}),
        }.get(ctx.media_kind)
        if api is None:
            raise RuntimeError(f"Telegram 不支持媒体类型 {ctx.media_kind}")
        await ctx.bot.call_api(api[0], chat_id=chat_id, **api[1])

    params: Dict[str, Any] = {"chat_id": chat_id, "text": plain_at_text(ctx.text) or " "}
    if ctx.reply_to:
        try:
            params["reply_to_message_id"] = int(ctx.reply_to)
        except ValueError:
            pass
    result = await ctx.bot.call_api("sendMessage", **params)
    return str((result or {}).get("message_id", ""))


async def _send_generic(bot: Any, target: str, channel_type: str, text: str) -> str:
    """通用适配器尽力而为发送：依次尝试常见 API 参数形态（at 文本化）。"""
    plain = plain_at_text(text)
    attempts: List[Dict[str, Any]] = [
        {"target": target, "message": plain},
        {"chat_id": target, "text": plain},
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
        OneBot v11 额外做深度增强：回复内容回捞（get_msg）与
        NapCat 本地图片零拷贝（get_image），对齐直连 QQ 频道体验。
        """
        if client is None:
            return
        wire = convert_event_to_wire(bot, event, adapter_keys)
        if wire is None:
            return
        if get_adapter_key(bot, adapter_keys) == "onebot_v11":
            await _enrich_onebot_event(bot, wire)
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
