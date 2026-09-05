"""HTTP 接口频道配置。

CONFIG_MODEL 是配置的唯一声明源：schema 经 agent/channel/config.py 派生注册到
ConfigRegistry（组 adapter/http_api，键 http_api_<field>），值统一存 ConfigManager。
"""

from __future__ import annotations

from pydantic import Field

from agent.channel.base import ChannelConfig


class HttpApiConfig(ChannelConfig):
    """HTTP 接口频道配置。"""

    enabled: bool = Field(default=False, description="是否启用 HTTP 接口频道")
    host: str = Field(default="127.0.0.1", description="HTTP 监听地址")
    port: int = Field(default=8091, description="HTTP 监听端口")
    reply_timeout: int = Field(default=60, description="等待 Agent 回复的超时时间（秒）")
    api_token: str = Field(
        default="", description="API Token（空则免认证）",
        json_schema_extra={"value_type": "password"})


CONFIG_MODEL = HttpApiConfig
