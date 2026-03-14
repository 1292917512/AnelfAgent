"""Telegram 适配器配置定义。"""

from __future__ import annotations

from typing import Any, Dict

from core.config import ConfigValueType

TELEGRAM_CONFIGS: Dict[str, Dict[str, Any]] = {
    "adapter/telegram": {
        "enabled": {
            "description": "是否启用 Telegram 频道",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "bot_token": {
            "description": "Bot Token（从 @BotFather 获取）",
            "default": "",
            "value_type": ConfigValueType.PASSWORD,
        },
        "proxy_host": {
            "description": "代理地址（留空不使用代理）",
            "default": "",
            "value_type": ConfigValueType.STRING,
        },
        "proxy_port": {
            "description": "代理端口",
            "default": 7890,
            "value_type": ConfigValueType.INTEGER,
        },
        "require_mention": {
            "description": "群聊中是否需要 @Bot 才触发思考（不配置则所有消息均触发）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "reply_to_mode": {
            "description": "回复引用策略",
            "default": "first",
            "value_type": ConfigValueType.ENUM,
            "options": ["first", "all", "off"],
        },
        "stream_mode": {
            "description": "流式输出模式",
            "default": "off",
            "value_type": ConfigValueType.ENUM,
            "options": ["off", "draft"],
        },
        "parse_mode": {
            "description": "消息格式化模式",
            "default": "html",
            "value_type": ConfigValueType.ENUM,
            "options": ["html", "plain"],
        },
        "link_preview": {
            "description": "是否启用链接预览",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "text_limit": {
            "description": "单条消息字符限制",
            "default": 4096,
            "value_type": ConfigValueType.INTEGER,
        },
        "webhook_enabled": {
            "description": "是否使用 Webhook 模式（否则使用长轮询）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "webhook_url": {
            "description": "Webhook 公开 URL",
            "default": "",
            "value_type": ConfigValueType.STRING,
        },
        "webhook_secret": {
            "description": "Webhook Secret Token",
            "default": "",
            "value_type": ConfigValueType.PASSWORD,
        },
        "webhook_port": {
            "description": "Webhook 监听端口",
            "default": 8443,
            "value_type": ConfigValueType.INTEGER,
        },
    }
}
