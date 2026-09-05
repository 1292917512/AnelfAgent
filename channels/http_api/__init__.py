"""HTTP 接口频道适配器。"""

from .adapter import HttpApiChannel
from .config import HttpApiConfig

CHANNEL_CLASS = HttpApiChannel
CONFIG_MODEL = HttpApiConfig

__all__ = ["HttpApiChannel"]
