"""NoneBot 桥接频道 — 完整 NoneBot 子进程客户端的统一桥接入口。

worker 子进程（独立 venv）承载 NoneBot2 + 全部适配器与插件，平台事件经
线协议回传本频道汇入 AI；AI 回复经粘性路由发往各平台。
"""

from .adapter import NoneBotBridgeChannel
from .config import NONEBOT_BRIDGE_CONFIGS

CHANNEL_CLASS = NoneBotBridgeChannel
CHANNEL_CONFIGS = NONEBOT_BRIDGE_CONFIGS

__all__ = ["NoneBotBridgeChannel"]
