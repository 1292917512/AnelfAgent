"""AcFun 频道适配器 — acfunsdk（账号密码登录 + 通知轮询 + 评论/弹幕出站 + 全量工具面）。"""

from .adapter import AcfunChannel
from .config import AcfunConfig

CHANNEL_CLASS = AcfunChannel
CONFIG_MODEL = AcfunConfig

__all__ = ["AcfunChannel"]
