"""wcauto 频道适配器 — 经 Windows 桥远程对接微信 4.x 桌面客户端（个人号）。"""

from .adapter import WcautoChannel
from .config import WcautoConfig

CHANNEL_CLASS = WcautoChannel
CONFIG_MODEL = WcautoConfig

__all__ = ["WcautoChannel"]
