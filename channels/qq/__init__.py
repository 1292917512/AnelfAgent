"""QQ 频道适配器 — 通过 OneBot v11 协议对接 NapCat / Lagrange 等。"""

from .adapter import OneBotV11Channel
from .config import QQConfig

CHANNEL_CLASS = OneBotV11Channel
CONFIG_MODEL = QQConfig

__all__ = ["OneBotV11Channel"]
