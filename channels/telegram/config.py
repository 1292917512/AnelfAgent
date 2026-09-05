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
    proxy_host: str = Field(default="", description="代理地址（留空不使用代理）")
    proxy_port: int = Field(default=7890, description="代理端口")
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
    webhook_enabled: bool = Field(
        default=False, description="是否使用 Webhook 模式（否则使用长轮询）")
    webhook_url: str = Field(default="", description="Webhook 公开 URL")
    webhook_secret: str = Field(
        default="", description="Webhook Secret Token",
        json_schema_extra={"value_type": "password"})
    webhook_port: int = Field(default=8443, description="Webhook 监听端口")


CONFIG_MODEL = TelegramConfig
