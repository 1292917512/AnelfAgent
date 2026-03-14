"""NoneBot 桥接频道 — 将 NoneBot 生态的所有适配器统一接入 AnelfTools 频道系统。

通过 event_preprocessor 钩子拦截 NoneBot 事件，转换为 AdapterMessage 后
交由 ChannelManager 分发给 AgentApp 处理。出站消息通过 NoneBot 的 Bot.send()
路由回对应平台。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Set

from agent.core.channel.channel import (
    BaseChannel,
    ChannelCapability,
    ChannelStatus,
    _err,
    _ok,
)
from core.log import log

from .config import NONEBOT_BRIDGE_CONFIGS


class NoneBotBridgeChannel(BaseChannel):
    """NoneBot 桥接频道 — NoneBot 所有适配器的统一入口。"""

    _entity_description = "NoneBot 桥接频道"
    _adapter_configs = NONEBOT_BRIDGE_CONFIGS

    def __init__(self) -> None:
        self._hooks_installed: bool = False
        self._bot_adapter_map: Dict[str, str] = {}
        super().__init__()

    @property
    def channel_id(self) -> str:
        return "nonebot_bridge"

    @property
    def display_name(self) -> str:
        return "NoneBot 桥接"

    @property
    def capabilities(self) -> Set[ChannelCapability]:
        return {ChannelCapability.SEND_TEXT}

    async def start(self) -> None:
        """启动 NoneBot 桥接。"""
        from .nonebot_init import init_nonebot, is_initialized

        adapter_names = self._cfg("adapters", [])
        env_config = self._cfg("nonebot_env", {})

        if not adapter_names:
            log("NoneBot Bridge: 未配置适配器列表，频道不启动", "WARNING")
            self._status = ChannelStatus.STOPPED
            return

        if not is_initialized():
            ok = init_nonebot(adapter_names, env_config)
            if not ok:
                self._status = ChannelStatus.ERROR
                return

        self._install_hooks()
        self._status = ChannelStatus.RUNNING
        log(f"NoneBot Bridge: 频道已启动，监听 {len(adapter_names)} 个适配器")

    async def stop(self) -> None:
        """停止 NoneBot 桥接。"""
        self._status = ChannelStatus.STOPPED
        log("NoneBot Bridge: 频道已停止")

    async def send_text(self, chat_id: str, text: str, **kwargs: Any) -> str:
        """通过 NoneBot Bot 发送文本消息。

        根据 chat_id 或 kwargs 中的 bot_id / adapter_key 定位 Bot 实例，
        再通过 NoneBot 的 call_api 发送消息。
        """
        from .nonebot_init import is_initialized

        if not is_initialized():
            return _err("NoneBot 未初始化")

        bot = self._resolve_bot(chat_id, **kwargs)
        if bot is None:
            return _err(f"未找到可用的 NoneBot Bot (chat_id={chat_id})")

        channel_type = kwargs.get("channel_type", "private")
        adapter_name = self._get_adapter_name_for_bot(bot)

        # NoneBot Bridge 发送纯文本，将 @ 格式转为可读文本
        text = self.normalize_at_mentions(text)

        try:
            result = await self._send_via_bot(bot, adapter_name, chat_id, text, channel_type)
            return _ok({"chat_id": chat_id, "adapter": adapter_name}) if result else _err("发送失败")
        except Exception as exc:
            log(f"NoneBot Bridge: 发送失败 - {exc}", "ERROR")
            return _err(f"发送异常: {exc}")

    def get_status_info(self) -> Dict[str, Any]:
        info = super().get_status_info()
        from .nonebot_init import get_nonebot_status
        info.update(get_nonebot_status())
        return info

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _cfg(self, key: str, default: Any = None) -> Any:
        return self.get_adapter_config(key, default)

    # ------------------------------------------------------------------
    # 事件钩子
    # ------------------------------------------------------------------

    def _install_hooks(self) -> None:
        """安装 NoneBot 事件钩子，桥接所有事件到 AnelfTools。"""
        if self._hooks_installed:
            return

        try:
            from nonebot.message import event_preprocessor
            from nonebot.exception import IgnoredException
            from nonebot import get_driver
        except ImportError:
            log("NoneBot Bridge: 无法导入 nonebot 模块，钩子未安装", "ERROR")
            return

        bridge = self
        intercept_all = self._cfg("intercept_all", True)

        @event_preprocessor
        async def _bridge_preprocessor(bot: Any, event: Any) -> None:
            """将 NoneBot 事件转换为 AdapterMessage 并分发到 ChannelManager。"""
            from .converter import convert_event

            adapter_msg = convert_event(bot, event)
            if adapter_msg is None:
                return

            # 记录 bot_id → adapter 映射
            bot_id = getattr(bot, "self_id", "")
            if bot_id:
                adapter_name = bridge._get_adapter_name_for_bot(bot)
                bridge._bot_adapter_map[bot_id] = adapter_name

            await bridge.on_message(adapter_msg)

            if intercept_all:
                raise IgnoredException("Handled by AnelfTools NoneBot Bridge")

        # Bot 连接/断开钩子
        driver = get_driver()

        @driver.on_bot_connect
        async def _on_bot_connect(bot: Any) -> None:
            bot_id = getattr(bot, "self_id", "?")
            adapter_name = bridge._get_adapter_name_for_bot(bot)
            bridge._bot_adapter_map[bot_id] = adapter_name
            log(f"NoneBot Bridge: Bot 已连接 - {bot_id} ({adapter_name})")

        @driver.on_bot_disconnect
        async def _on_bot_disconnect(bot: Any) -> None:
            bot_id = getattr(bot, "self_id", "?")
            bridge._bot_adapter_map.pop(bot_id, None)
            log(f"NoneBot Bridge: Bot 已断开 - {bot_id}")

        self._hooks_installed = True
        log("NoneBot Bridge: 事件钩子已安装")

    # ------------------------------------------------------------------
    # Bot 路由与发送
    # ------------------------------------------------------------------

    def _resolve_bot(self, chat_id: str, **kwargs: Any) -> Optional[Any]:
        """根据参数定位合适的 NoneBot Bot 实例。"""
        import nonebot

        bots = nonebot.get_bots()
        if not bots:
            return None

        # 优先使用指定的 bot_id
        bot_id = kwargs.get("bot_id", "")
        if bot_id and bot_id in bots:
            return bots[bot_id]

        # 优先匹配适配器类型
        adapter_key = kwargs.get("adapter_key", "")
        if adapter_key:
            for bid, bot in bots.items():
                if self._bot_adapter_map.get(bid, "") == adapter_key:
                    return bot

        # 回退：返回第一个可用 Bot
        return next(iter(bots.values()))

    @staticmethod
    def _get_adapter_name_for_bot(bot: Any) -> str:
        """获取 Bot 关联的适配器名称。"""
        adapter = getattr(bot, "adapter", None)
        if adapter is not None:
            try:
                return str(type(adapter).get_name()).lower().replace(" ", "_")
            except (AttributeError, TypeError):
                pass
        return "unknown"

    @staticmethod
    async def _send_via_bot(
        bot: Any,
        adapter_name: str,
        chat_id: str,
        text: str,
        channel_type: str,
    ) -> bool:
        """通过 NoneBot Bot 实例发送消息。

        对不同适配器使用最通用的 API 调用方式。
        """
        try:
            if "onebot" in adapter_name:
                return await _send_onebot(bot, chat_id, text, channel_type)
            # 通用方式：尝试 send_msg API
            await bot.send_msg(message=text, user_id=chat_id)
            return True
        except AttributeError:
            pass

        # 最终回退：call_api
        try:
            await bot.call_api("send_message", message=text, target=chat_id)
            return True
        except Exception as exc:
            log(f"NoneBot Bridge: 通用发送失败 ({adapter_name}) - {exc}", "WARNING")
            return False


async def _send_onebot(bot: Any, chat_id: str, text: str, channel_type: str) -> bool:
    """OneBot 专用发送逻辑。"""
    try:
        if channel_type == "group":
            await bot.call_api(
                "send_group_msg",
                group_id=int(chat_id),
                message=text,
            )
        else:
            await bot.call_api(
                "send_msg",
                message_type="private",
                user_id=int(chat_id),
                message=text,
            )
        return True
    except Exception as exc:
        log(f"NoneBot Bridge: OneBot 发送失败 - {exc}", "WARNING")
        return False


CHANNEL_CLASS = NoneBotBridgeChannel
