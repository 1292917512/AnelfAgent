"""WebUI 频道配置。

CONFIG_MODEL 是配置的唯一声明源：schema 经 agent/channel/config.py 派生注册到
ConfigRegistry（组 adapter/webui，键 webui_<field>），值统一存 ConfigManager。
"""

from __future__ import annotations

from pydantic import Field

from agent.channel.base import ChannelConfig


class WebUIConfig(ChannelConfig):
    """WebUI 频道配置。"""

    enabled: bool = Field(default=True, description="是否启用 WebUI 频道")
    deferred_start: bool = Field(
        default=True, description="延迟启动（随 Web 服务器就绪后再启动，避免启动顺序竞争）",
        json_schema_extra={"advanced": True})


CONFIG_MODEL = WebUIConfig
