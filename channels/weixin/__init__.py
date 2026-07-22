"""微信频道适配器 — 通过腾讯 iLink Bot API 接入个人微信。"""

from .adapter import WeixinChannel
from .config import WEIXIN_CONFIGS

CHANNEL_CLASS = WeixinChannel
CHANNEL_CONFIGS = WEIXIN_CONFIGS

__all__ = ["CHANNEL_CLASS", "CHANNEL_CONFIGS", "WeixinChannel"]
