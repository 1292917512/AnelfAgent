"""HTTP 接口频道适配器。"""

from .adapter import HttpApiChannel
from .config import HTTP_API_CONFIGS

CHANNEL_CLASS = HttpApiChannel
CHANNEL_CONFIGS = HTTP_API_CONFIGS
ENABLED_KEY = "http_api_enabled"

__all__ = ["HttpApiChannel"]
