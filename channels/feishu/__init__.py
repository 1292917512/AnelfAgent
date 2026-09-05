"""飞书频道适配器。"""

from .adapter import FeishuChannel
from .config import FeishuConfig

CHANNEL_CLASS = FeishuChannel
CONFIG_MODEL = FeishuConfig

__all__ = ["CHANNEL_CLASS", "FeishuChannel"]
