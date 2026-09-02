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
        "webhook_host": {
            "description": "Webhook 监听地址（仅 webhook 模式使用；非回环地址必须配置验证令牌或加密 Key，否则拒绝启动）",
            "default": "127.0.0.1",
            "value_type": ConfigValueType.STRING,
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
        "reply_to_mode": {
            "description": "回复引用策略（first=仅首条分段挂引用 / all=全部挂引用 / off=不引用）",
            "default": "first",
            "value_type": ConfigValueType.ENUM,
            "options": ["first", "all", "off"],
        },
        "reply_in_thread": {
            "description": "群聊中引用回复进入话题（thread）模式，长讨论不刷群主聊面板",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "markdown_render": {
            "description": "含 Markdown 语法（标题/加粗/列表/表格/代码块等）的回复以富文本渲染发送",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "sender_name_enabled": {
            "description": "解析发送者昵称（需为应用开通 contact:user.base:readonly 权限；缺失时自动降级为 open_id）",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "max_download_mb": {
            "description": "入站媒体文件下载大小上限（MB），超出拒绝下载并在日志中说明",
            "default": 50,
            "value_type": ConfigValueType.INTEGER,
        },
        "text_limit": {
            "description": "单条消息字符限制（飞书上限约 4000）",
            "default": 4000,
            "value_type": ConfigValueType.INTEGER,
        },
    }
}
