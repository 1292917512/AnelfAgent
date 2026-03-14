"""飞书频道适配器。"""

from .adapter import FeishuChannel

CHANNEL_CLASS = FeishuChannel

__all__ = ["CHANNEL_CLASS", "FeishuChannel"]
