"""Telegram 频道配置。

CONFIG_MODEL 是配置的唯一声明源：schema 经 agent/channel/config.py 派生注册到
ConfigRegistry（组 adapter/telegram，键 telegram_<field>），值统一存 ConfigManager。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agent.channel.base import ChannelConfig


class TelegramConfig(ChannelConfig):
    """Telegram 频道配置（pydantic 强类型）。"""

    enabled: bool = Field(default=False, description="是否启用 Telegram 频道")
    bot_token: str = Field(
        default="", description="Bot Token（从 @BotFather 获取）",
        json_schema_extra={"value_type": "password"})
    proxy_mode: Literal["system", "direct", "proxy"] = Field(
        default="system", description="网络策略：system 全局规则 / direct 直连 / proxy 全局代理",
        json_schema_extra={"related_key": "proxy_enabled"})
    require_mention: bool = Field(
        default=False, description="群聊中是否需要 @Bot 才触发思考（不配置则所有消息均触发）")
    channel_post_trigger: bool = Field(
        default=False, description="频道帖子是否触发思考（默认关闭，帖子仅感知入窗不唤醒 Mind）")
    reply_to_mode: Literal["first", "all", "off"] = Field(
        default="first", description="回复引用策略")
    stream_mode: Literal["off", "draft"] = Field(default="off", description="流式输出模式")
    parse_mode: Literal["html", "plain"] = Field(default="html", description="消息格式化模式")
    link_preview: bool = Field(default=True, description="是否启用链接预览")
    text_limit: int = Field(default=4096, description="单条消息字符限制")


CONFIG_MODEL = TelegramConfig
