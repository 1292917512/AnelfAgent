"""QQ 开放平台频道配置，供配置中心与频道运行时共用。"""

from typing import Literal

from pydantic import Field

from agent.channel.base import ChannelConfig

GATEWAY_INTENTS: dict[str, int] = {
    "chats": 1 << 25, "mentions": 1 << 30, "direct": 1 << 12, "interactions": 1 << 26,
    "members": 1 << 24, "reactions": 1 << 10, "audit": 1 << 27, "guilds": 1, "messages": 1 << 9,
}


class QQBotConfig(ChannelConfig):
    """单个 QQ 开放平台机器人的凭证、事件订阅与入站策略。"""

    enabled: bool = Field(default=False, description="启用 QQ 官方机器人频道", json_schema_extra={"tag": "startup"})
    app_id: str = Field(default="", coerce_numbers_to_str=True, description="开放平台 AppID", json_schema_extra={"tag": "account"})
    app_secret: str = Field(default="", description="开放平台 AppSecret",
                            json_schema_extra={"value_type": "password", "tag": "account"}, repr=False)
    connection_mode: Literal["webhook", "websocket"] = Field(
        default="webhook", description="事件接入方式，Webhook 需要公网 HTTPS 回调地址", json_schema_extra={"tag": "transport"})
    intents: int = Field(default=GATEWAY_INTENTS["chats"] | GATEWAY_INTENTS["mentions"], ge=1, le=2**31-1,
                         description="WebSocket 事件位掩码，默认订阅单聊、群聊和频道 @ 消息",
                         json_schema_extra={"tag": "subscriptions"})
    require_mention: bool = Field(default=True, description="群聊和子频道中，仅 @ 机器人时触发思考")
    allowed_chats: str = Field(default="", description="允许入站的会话目标，英文逗号分隔，留空允许全部")


CONFIG_MODEL = QQBotConfig
