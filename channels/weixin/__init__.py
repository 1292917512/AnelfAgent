"""微信频道适配器 — 通过腾讯 iLink Bot API 接入个人微信。"""

from .adapter import WeixinChannel
from .config import WeixinConfig

CHANNEL_CLASS = WeixinChannel
CONFIG_MODEL = WeixinConfig

__all__ = ["CHANNEL_CLASS", "CONFIG_MODEL", "WeixinChannel"]
