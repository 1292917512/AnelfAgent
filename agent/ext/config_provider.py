"""兼容层：config_provider 已迁移到 agent.core.config。"""

from agent.core.config import (  # noqa: F401
    BotConfig,
    BotConfigProvider,
    LLMConfig,
    MindConfig,
    get_config_provider,
)

__all__ = [
    "BotConfig",
    "BotConfigProvider",
    "LLMConfig",
    "MindConfig",
    "get_config_provider",
]
