"""NoneBot 桥接频道配置。

定义桥接频道的配置项和已知适配器注册表。
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.config import ConfigValueType

# 已知 NoneBot 适配器及其 pip 包名 / 导入路径
KNOWN_ADAPTERS: Dict[str, Dict[str, str]] = {
    "onebot_v11": {
        "label": "OneBot V11",
        "package": "nonebot-adapter-onebot",
        "import": "nonebot.adapters.onebot.v11",
        "class": "Adapter",
    },
    "onebot_v12": {
        "label": "OneBot V12",
        "package": "nonebot-adapter-onebot",
        "import": "nonebot.adapters.onebot.v12",
        "class": "Adapter",
    },
    "qq": {
        "label": "QQ",
        "package": "nonebot-adapter-qq",
        "import": "nonebot.adapters.qq",
        "class": "Adapter",
    },
    "discord": {
        "label": "Discord",
        "package": "nonebot-adapter-discord",
        "import": "nonebot.adapters.discord",
        "class": "Adapter",
    },
    "telegram": {
        "label": "Telegram",
        "package": "nonebot-adapter-telegram",
        "import": "nonebot.adapters.telegram",
        "class": "Adapter",
    },
    "feishu": {
        "label": "飞书",
        "package": "nonebot-adapter-feishu",
        "import": "nonebot.adapters.feishu",
        "class": "Adapter",
    },
    "kook": {
        "label": "Kook / 开黑啦",
        "package": "nonebot-adapter-kaiheila",
        "import": "nonebot.adapters.kaiheila",
        "class": "Adapter",
    },
    "satori": {
        "label": "Satori",
        "package": "nonebot-adapter-satori",
        "import": "nonebot.adapters.satori",
        "class": "Adapter",
    },
    "dodo": {
        "label": "DoDo",
        "package": "nonebot-adapter-dodo",
        "import": "nonebot.adapters.dodo",
        "class": "Adapter",
    },
    "console": {
        "label": "Console",
        "package": "nonebot-adapter-console",
        "import": "nonebot.adapters.console",
        "class": "Adapter",
    },
}

NONEBOT_BRIDGE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "adapter/nonebot_bridge": {
        "enabled": {
            "description": "是否启用 NoneBot 桥接频道",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "adapters": {
            "description": "要加载的 NoneBot 适配器列表（如 [\"onebot_v11\"]）",
            "default": [],
            "value_type": ConfigValueType.JSON,
        },
        "nonebot_env": {
            "description": "NoneBot .env 配置文件内容（键值对 JSON）",
            "default": {},
            "value_type": ConfigValueType.JSON,
        },
        "intercept_all": {
            "description": "是否拦截所有 NoneBot 事件（True=由 AnelfTools 处理，False=同时保留 NoneBot 插件处理）",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
    }
}


def get_default_channel_config() -> Dict[str, Any]:
    """返回 channel_config.json 的默认内容。"""
    return {
        "enabled": False,
        "adapters": [],
        "nonebot_env": {},
        "intercept_all": True,
    }
