"""Minecraft 聊天桥接配置。"""

from pydantic import Field

from agent.channel.base import ChannelConfig


class MinecraftConfig(ChannelConfig):
    """连接现有 Minecraft MCP 执行器的频道配置。"""

    enabled: bool = Field(default=False, description="启用 Minecraft 游戏聊天")
    mcp_server: str = Field(default="minecraft", min_length=1, description="插件注册的 MCP 服务名")
    server_id: str = Field(default="local", pattern=r"^[a-zA-Z0-9_-]+$", description="世界标识，用于隔离会话")
    bot_username: str = Field(default="AnelfBot", description="机器人游戏名，用于过滤自身消息和识别 @")
    allowed_players: list[str] = Field(
        default_factory=list,
        description="允许交互的玩家名，空列表表示本服全部玩家",
        json_schema_extra={"value_type": "json"},
    )
    require_mention: bool = Field(
        default=False,
        description="公共聊天需要 @机器人才回应；陪玩建议关闭（玩家说话即唤醒）",
    )
    poll_interval_seconds: float = Field(
        default=1.0,
        ge=0.25,
        le=30,
        description="游戏聊天事件轮询间隔",
        json_schema_extra={"unit": "s", "min": 0.25, "max": 30, "advanced": True},
    )


CONFIG_MODEL = MinecraftConfig
