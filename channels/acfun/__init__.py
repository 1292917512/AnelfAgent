"""AcFun 频道适配器 — acfunsdk（账号密码登录 + 通知轮询 + 评论/弹幕出站 + 全量工具面）。"""

from .adapter import AcfunChannel
from .config import ACFUN_CONFIGS

CHANNEL_CLASS = AcfunChannel
CHANNEL_CONFIGS = ACFUN_CONFIGS
ENABLED_KEY = "acfun_enabled"

__all__ = ["AcfunChannel"]
