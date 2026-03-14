"""QQ 频道配置 — 通过 OneBot v11 协议对接 NapCat / Lagrange 等。"""

from __future__ import annotations

from core.config import ConfigValueType

ONEBOT_V11_CONFIGS = {
    "adapter/qq": {
        "enabled": {
            "description": "是否启用 QQ 频道",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "ws_mode": {
            "description": "连接模式",
            "default": "reverse",
            "value_type": ConfigValueType.ENUM,
            "options": ["forward", "reverse"],
        },
        "ws_url": {
            "description": "NapCat WS 地址（如 ws://127.0.0.1:3001）",
            "default": "ws://127.0.0.1:3001",
            "value_type": ConfigValueType.STRING,
            "tag": "forward",
        },
        "reconnect_interval": {
            "description": "断线重连间隔（秒）",
            "default": 5,
            "value_type": ConfigValueType.INTEGER,
            "tag": "forward",
        },
        "max_reconnect_attempts": {
            "description": "最大重连次数（0 = 无限重试）",
            "default": 0,
            "value_type": ConfigValueType.INTEGER,
            "tag": "forward",
        },
        "reverse_ws_port": {
            "description": "本地 WS Server 监听端口（NapCat 连接此端口）",
            "default": 8095,
            "value_type": ConfigValueType.INTEGER,
            "tag": "reverse",
        },
        "access_token": {
            "description": "访问令牌（与 NapCat 配置一致，留空则不鉴权）",
            "default": "",
            "value_type": ConfigValueType.PASSWORD,
        },
        "http_api_url": {
            "description": "HTTP API 地址（可选，留空则通过 WS 发送）",
            "default": "",
            "value_type": ConfigValueType.STRING,
        },
        "self_id": {
            "description": "Bot QQ 号（可选，用于判断 @bot）",
            "default": "",
            "value_type": ConfigValueType.STRING,
        },
        "napcat_webui_url": {
            "description": "NapCat WebUI 地址（连接成功后可在通道页内嵌浏览）",
            "default": "http://127.0.0.1:6099/webui/",
            "value_type": ConfigValueType.URL,
        },
        "require_mention": {
            "description": "群聊中是否需要 @ bot 才激活思考（私聊不受影响，所有消息仍会记录到对话历史）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "whitelist_enabled": {
            "description": "是否启用白名单（开启后仅白名单内的群/用户消息会被处理）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "group_whitelist": {
            "description": "群白名单（允许接收消息的群号，多个用英文逗号分隔）",
            "default": "",
            "value_type": ConfigValueType.TEXT,
        },
        "user_whitelist": {
            "description": "用户白名单（允许私聊的 QQ 号，多个用英文逗号分隔）",
            "default": "",
            "value_type": ConfigValueType.TEXT,
        },
    }
}
