"""NoneBot 桥接频道 — 将 NoneBot 支持的所有适配器统一接入 AnelfTools 频道系统。"""

from .adapter import NoneBotBridgeChannel
from .config import NONEBOT_BRIDGE_CONFIGS

CHANNEL_CLASS = NoneBotBridgeChannel
CHANNEL_CONFIGS = NONEBOT_BRIDGE_CONFIGS

__all__ = ["NoneBotBridgeChannel"]
