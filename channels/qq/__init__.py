"""QQ 频道适配器 — 通过 OneBot v11 协议对接 NapCat / Lagrange 等。"""

from .adapter import OneBotV11Channel
from .config import ONEBOT_V11_CONFIGS

CHANNEL_CLASS = OneBotV11Channel
CHANNEL_CONFIGS = ONEBOT_V11_CONFIGS
ENABLED_KEY = "qq_enabled"

__all__ = ["OneBotV11Channel"]
