"""Telegram Bot 频道适配器。"""

from .adapter import TelegramAdapter
from .config import TELEGRAM_CONFIGS

CHANNEL_CLASS = TelegramAdapter
CHANNEL_CONFIGS = TELEGRAM_CONFIGS
ENABLED_KEY = "telegram_enabled"

__all__ = ["TelegramAdapter"]
