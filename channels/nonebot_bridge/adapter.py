"""NoneBot 桥接频道 — 将 NoneBot 生态的所有适配器统一接入 AnelfTools 频道系统。

通过 event_preprocessor 钩子拦截 NoneBot 事件，转换为 AdapterMessage 后
交由 ChannelManager 分发给 AgentApp 处理。出站消息通过 NoneBot 的 Bot.send()
路由回对应平台。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Set

from pydantic import Field

from agent.channel.base import BaseChannel, ChannelConfig, ChannelMetadata
from agent.channel.channel_types import (
    ChannelCapability,
    ChannelStatus,
    _err,
    _ok,
)
from agent.channel.schemas import (
    AdapterChannel, ChannelType, SendRequest, SendResponse, SendSegment,
    ChannelInfo, ChannelUser, ChannelUserRole, HealthStatus,
)
from core.log import log

from .config import NONEBOT_BRIDGE_CONFIGS




class NoneBotBridgeConfig(ChannelConfig):
    """NoneBot 桥接 频道配置。"""

    nonebot_url: str = Field(default="http://127.0.0.1:8080", description="NoneBot 服务地址")
    access_token: str = Field(default="", description="访问令牌")


class NoneBotBridgeChannel(BaseChannel[NoneBotBridgeConfig]):
    """NoneBot 桥接频道 — NoneBot 所有适配器的统一入口。"""

    _entity_description = "NoneBot 桥接频道"

    metadata = ChannelMetadata(
        name="NoneBot Bridge",
        description="桥接外部 NoneBot2 实例，复用其平台生态",
        version="2.0.0",
        author="AnelfAgent",
    )
    _Configs = NoneBotBridgeConfig
    _adapter_configs = NONEBOT_BRIDGE_CONFIGS

    def __init__(self) -> None:
        self._hooks_installed: bool = False
        self._bot_adapter_map: Dict[str, str] = {}
        super().__init__()

    channel_id = "nonebot_bridge"

    display_name = "NoneBot 桥接"

    capabilities: Set[ChannelCapability] = {ChannelCapability.SEND_TEXT}

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
        """读取配置（使用 self.config）。"""
        return getattr(self.config, key, default)

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

    # ------------------------------------------------------------------
    # BaseChannel 协议方法
    # ------------------------------------------------------------------

    async def forward_message(self, request: SendRequest) -> SendResponse:
        """统一发送入口。"""
        try:
            chat_id = request.channel.channel_id
            text_parts = [seg.content for seg in request.segments if seg.type.value == "text"]
            full_text = "\n".join(text_parts) if text_parts else ""
            result_json = await self.send_text(chat_id, full_text)
            result = json.loads(result_json)
            if result.get("success"):
                return SendResponse(success=True, message_id=result.get("message_id"))
            return SendResponse(success=False, error=result.get("error", "unknown"))
        except Exception as exc:
            return SendResponse(success=False, error=str(exc))

    async def get_self_info(self) -> ChannelUser:
        return ChannelUser(
            platform=self.channel_id,
            user_id="nonebot_bridge_bot",
            user_name="NoneBot Bridge",
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
            channel_type=ChannelType.PRIVATE,
        )

    async def health_check(self) -> HealthStatus:
        try:
            started = time.time()
            return HealthStatus(
                healthy=True,
                detail=f"NoneBot bridge OK: {self.config.nonebot_url}",
                latency_ms=(time.time() - started) * 1000,
                last_success_at=time.time(),
            )
        except Exception as exc:
            return HealthStatus(healthy=False, detail=str(exc), last_error=str(exc))

    async def render_approval_prompt(self, ctx) -> SendRequest:
        """渲染批准提示（NoneBot 文本提示）。"""
        from agent.channel.base import ApprovalPromptRenderContext

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
