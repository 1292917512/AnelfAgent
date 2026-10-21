"""CLI 频道配置声明。

Model Experience: 仅终端输入经标准频道消息链进入模型，不注入额外提示。
Token effect: 不增加固定 token；消息按普通会话计入。
Cache effect: 不修改稳定前缀，终端消息进入动态会话上下文。
"""

from __future__ import annotations

from pydantic import Field

from agent.channel.base import ChannelConfig


class CLIConfig(ChannelConfig):
    """终端调试频道的配置。"""

    enabled: bool = Field(default=False, description="启用终端频道")
    show_prompt: bool = Field(default=True, description="显示输入提示符")
    bot_name: str = Field(default="", description="Bot 显示名（留空从人设读取）")


CONFIG_MODEL = CLIConfig
