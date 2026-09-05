"""Telegram Bot 频道适配器。"""

from .adapter import TelegramAdapter
from .config import TelegramConfig

CHANNEL_CLASS = TelegramAdapter
CONFIG_MODEL = TelegramConfig

__all__ = ["TelegramAdapter"]
