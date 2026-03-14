"""飞书适配器配置定义。"""

from __future__ import annotations

from typing import Any, Dict

from core.config import ConfigValueType

FEISHU_CONFIGS: Dict[str, Dict[str, Any]] = {
    "adapter/feishu": {
        "app_id": {
            "description": "飞书应用 App ID（在飞书开放平台创建应用后获取）",
            "default": "",
            "value_type": ConfigValueType.STRING,
        },
        "app_secret": {
            "description": "飞书应用 App Secret",
            "default": "",
            "value_type": ConfigValueType.PASSWORD,
        },
        "domain": {
            "description": "飞书域名（feishu=国内版, lark=国际版）",
            "default": "feishu",
            "value_type": ConfigValueType.ENUM,
            "options": ["feishu", "lark"],
        },
        "connection_mode": {
            "description": "接入模式（websocket=长连接免公网, webhook=HTTP 回调需公网）",
            "default": "websocket",
            "value_type": ConfigValueType.ENUM,
            "options": ["websocket", "webhook"],
        },
        "encrypt_key": {
            "description": "事件加密密钥（Webhook 模式需要，在飞书后台「事件订阅」中获取）",
            "default": "",
            "value_type": ConfigValueType.PASSWORD,
        },
        "verification_token": {
            "description": "事件验证令牌（Webhook 模式需要，在飞书后台「事件订阅」中获取）",
            "default": "",
            "value_type": ConfigValueType.PASSWORD,
        },
        "webhook_port": {
            "description": "Webhook 监听端口（仅 webhook 模式使用）",
            "default": 9321,
            "value_type": ConfigValueType.INTEGER,
        },
        "require_mention": {
            "description": "群聊中是否需要 @Bot 才触发思考",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "text_limit": {
            "description": "单条消息字符限制（飞书上限约 4000）",
            "default": 4000,
            "value_type": ConfigValueType.INTEGER,
        },
    }
}
