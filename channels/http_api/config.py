"""HTTP 接口频道配置定义。"""

from __future__ import annotations

from typing import Any, Dict

from core.config import ConfigValueType

HTTP_API_CONFIGS: Dict[str, Dict[str, Any]] = {
    "adapter/http_api": {
        "enabled": {
            "description": "是否启用 HTTP 接口频道",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "host": {
            "description": "HTTP 监听地址",
            "default": "127.0.0.1",
            "value_type": ConfigValueType.STRING,
        },
        "port": {
            "description": "HTTP 监听端口",
            "default": 8091,
            "value_type": ConfigValueType.INTEGER,
        },
        "reply_timeout": {
            "description": "等待 Agent 回复的超时时间（秒）",
            "default": 60,
            "value_type": ConfigValueType.INTEGER,
        },
    }
}
