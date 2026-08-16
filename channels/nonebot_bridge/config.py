"""NoneBot 桥接频道配置 — 适配器注册表（含各平台接入元数据）与配置项 schema。

适配器注册表分两层：
- ``KNOWN_ADAPTERS``：内置精选适配器，带完整的平台接入元数据
  （所需环境变量、接入难度、说明、文档链接），Web 界面据此渲染对接表单；
- 注册表动态适配器：启动/刷新时从 ``registry.nonebot.dev/adapters.json``
  合并非内置项（见 services/nonebot.py），由通用转换兜底，支持可拓展开发。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from core.config import ConfigValueType


@dataclass
class AdapterEnvKey:
    """平台接入所需的单个环境变量描述。"""

    key: str
    """环境变量名（写入 worker .env）"""
    label: str
    """展示说明"""
    secret: bool = False
    """是否按敏感值处理（UI 遮盖）"""
    json_mode: bool = False
    """是否为 JSON 结构（UI 提供多行 JSON 编辑）"""
    placeholder: str = ""
    """示例值（不落盘，仅展示）"""


@dataclass
class AdapterSetup:
    """平台接入指引元数据。"""

    difficulty: str = "easy"
    """easy / medium / hard"""
    env_keys: List[AdapterEnvKey] = field(default_factory=list)
    notes: str = ""
    docs: str = ""


KNOWN_ADAPTERS: Dict[str, Dict[str, Any]] = {
    "onebot_v11": {
        "label": "OneBot V11 (QQ)",
        "package": "nonebot-adapter-onebot",
        "import": "nonebot.adapters.onebot.v11",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="easy",
            env_keys=[
                AdapterEnvKey(
                    key="ONEBOT_ACCESS_TOKEN",
                    label="访问令牌（与 OneBot 实现端一致，反向 WS 部署在非回环地址时必填）",
                    secret=True,
                ),
                AdapterEnvKey(
                    key="ONEBOT_WS_URLS",
                    label="正向 WS 地址列表（连 NapCat/Lagrange 的 WS 服务端时填写，留空则用反向 WS）",
                    json_mode=True,
                    placeholder='["ws://127.0.0.1:3001/"]',
                ),
            ],
            notes=(
                "推荐反向 WS：在 NapCat/Lagrange 中把反向 WS 地址指向本页展示的 worker 地址"
                "（ws://<主机>:<worker端口>/onebot/v11/ws）。正向 WS 则填写 ONEBOT_WS_URLS。"
            ),
            docs="https://onebot.adapters.nonebot.dev/",
        ),
    },
    "onebot_v12": {
        "label": "OneBot V12",
        "package": "nonebot-adapter-onebot",
        "import": "nonebot.adapters.onebot.v12",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="medium",
            env_keys=[
                AdapterEnvKey(
                    key="ONEBOT_WS_URLS",
                    label="正向 WS 地址列表（OneBot V12 服务端地址）",
                    json_mode=True,
                    placeholder='["ws://127.0.0.1:8080/"]',
                ),
                AdapterEnvKey(key="ONEBOT_ACCESS_TOKEN", label="访问令牌", secret=True),
            ],
            notes="OneBot V12 统一终端协议，正向 WS 连接 OneBot V12 实现。",
            docs="https://onebot.adapters.nonebot.dev/",
        ),
    },
    "qq": {
        "label": "QQ 官方 (频道/群)",
        "package": "nonebot-adapter-qq",
        "import": "nonebot.adapters.qq",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="medium",
            env_keys=[
                AdapterEnvKey(
                    key="QQ_BOTS",
                    label="QQ 开放平台机器人凭据列表（appid / token / secret / intent）",
                    json_mode=True,
                    placeholder=(
                        '[{"id": "appid", "token": "令牌", "secret": "密钥",'
                        ' "intent": {"guild_messages": true, "at_messages": true}}]'
                    ),
                ),
            ],
            notes="在 q.qq.com 开放平台创建机器人后获取 AppID/Token/Secret；支持频道与群聊场景。",
            docs="https://github.com/nonebot/adapter-qq",
        ),
    },
    "discord": {
        "label": "Discord",
        "package": "nonebot-adapter-discord",
        "import": "nonebot.adapters.discord",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="medium",
            env_keys=[
                AdapterEnvKey(
                    key="DISCORD_BOTS",
                    label="Discord 机器人凭据列表",
                    json_mode=True,
                    placeholder='[{"token": "机器人令牌"}]',
                ),
            ],
            notes="在 Discord Developer Portal 创建 Bot 获取 Token；国内网络需配置代理（https_proxy）。",
            docs="https://github.com/nonebot/adapter-discord",
        ),
    },
    "telegram": {
        "label": "Telegram",
        "package": "nonebot-adapter-telegram",
        "import": "nonebot.adapters.telegram",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="easy",
            env_keys=[
                AdapterEnvKey(
                    key="TELEGRAM_BOTS",
                    label="Telegram 机器人凭据列表（@BotFather 获取 Token）",
                    json_mode=True,
                    placeholder='[{"token": "123456:ABC-DEF..."}]',
                ),
                AdapterEnvKey(
                    key="TELEGRAM_WEBHOOK_URL",
                    label="Webhook 公网地址（可选，留空使用内置 webhook 中转）",
                ),
            ],
            notes="向 @BotFather 申请 Bot Token；国内网络需配置代理（https_proxy 环境变量）。",
            docs="https://github.com/nonebot/adapter-telegram",
        ),
    },
    "feishu": {
        "label": "飞书",
        "package": "nonebot-adapter-feishu",
        "import": "nonebot.adapters.feishu",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="medium",
            env_keys=[
                AdapterEnvKey(
                    key="FEISHU_BOTS",
                    label="飞书应用凭据列表（app_id / app_secret / 事件订阅令牌）",
                    json_mode=True,
                    placeholder=(
                        '[{"app_id": "cli_xxx", "app_secret": "密钥",'
                        ' "verification_token": "事件验证令牌", "encrypt_key": "加密密钥"}]'
                    ),
                ),
            ],
            notes="在飞书开放平台创建企业自建应用，开启机器人能力并配置事件订阅回调。",
            docs="https://feishu.adapters.nonebot.dev/",
        ),
    },
    "kook": {
        "label": "KOOK / 开黑啦",
        "package": "nonebot-adapter-kaiheila",
        "import": "nonebot.adapters.kaiheila",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="easy",
            env_keys=[
                AdapterEnvKey(
                    key="KAIHEILA_BOTS",
                    label="KOOK 机器人凭据列表（开发者后台获取 Token）",
                    json_mode=True,
                    placeholder='[{"token": "1/xxx/xxx/xxx=="}]',
                ),
            ],
            notes="在 KOOK 开发者中心创建机器人获取 Token，WebSocket 接入无需公网。",
            docs="https://github.com/Tian-que/nonebot-adapter-kaiheila",
        ),
    },
    "satori": {
        "label": "Satori (协议聚合)",
        "package": "nonebot-adapter-satori",
        "import": "nonebot.adapters.satori",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="medium",
            env_keys=[
                AdapterEnvKey(
                    key="SATORI_CLIENTS",
                    label="Satori 协议服务端列表（host / port / token）",
                    json_mode=True,
                    placeholder='[{"host": "127.0.0.1", "port": 5140, "token": "令牌"}]',
                ),
            ],
            notes="Satori 是跨平台通用协议，经 Chronocat 等实现可同时接入 QQ 等多个平台。",
            docs="https://github.com/nonebot/adapter-satori",
        ),
    },
    "dodo": {
        "label": "DoDo",
        "package": "nonebot-adapter-dodo",
        "import": "nonebot.adapters.dodo",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="medium",
            env_keys=[
                AdapterEnvKey(
                    key="DODO_BOTS",
                    label="DoDo 机器人凭据列表（client_id / token）",
                    json_mode=True,
                    placeholder='[{"client_id": "xxx", "token": "xxx"}]',
                ),
            ],
            notes="在 DoDo 开放平台创建机器人获取 Client ID 与 Token。",
            docs="https://github.com/nonebot/adapter-dodo",
        ),
    },
    "console": {
        "label": "Console (终端)",
        "package": "nonebot-adapter-console",
        "import": "nonebot.adapters.console",
        "class": "Adapter",
        "setup": AdapterSetup(
            difficulty="easy",
            env_keys=[],
            notes="终端控制台适配器，无需任何配置，适合快速验证桥接链路（在 worker 日志中交互）。",
            docs="https://github.com/nonebot/adapter-console",
        ),
    },
}


def get_registry_adapters_path() -> str:
    """内置适配器注册表快照缓存路径（注册表动态适配器元数据持久化）。"""
    from core.path import ConfigPaths

    return str(ConfigPaths.NONEBOT_DIR) + "/registry_adapters.json"


NONEBOT_BRIDGE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "adapter/nonebot_bridge": {
        "enabled": {
            "description": "是否启用 NoneBot 桥接频道（子进程客户端）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "adapters": {
            "description": "要加载的 NoneBot 适配器列表（如 [\"onebot_v11\"]）",
            "default": [],
            "value_type": ConfigValueType.JSON,
        },
        "plugins": {
            "description": "要加载的 NoneBot 插件模块名列表",
            "default": [],
            "value_type": ConfigValueType.JSON,
        },
        "nonebot_env": {
            "description": "写入 worker .env 的配置键值对（各平台凭据等）",
            "default": {},
            "value_type": ConfigValueType.JSON,
        },
        "intercept_all": {
            "description": "拦截所有平台事件仅供 AI 处理（False=插件与 AI 同时收到消息）",
            "default": False,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "bridge_ws_port": {
            "description": "桥接 WS 服务端口（worker 子进程回连主进程）",
            "default": 8197,
            "value_type": ConfigValueType.INTEGER,
        },
        "worker_host": {
            "description": "worker 子进程 HTTP/WS 监听地址（平台反向接入用）",
            "default": "127.0.0.1",
        },
        "worker_port": {
            "description": "worker 子进程 HTTP/WS 监听端口（平台反向接入用）",
            "default": 8198,
            "value_type": ConfigValueType.INTEGER,
        },
        "auto_restart": {
            "description": "worker 子进程异常退出后自动重启",
            "default": True,
            "value_type": ConfigValueType.BOOLEAN,
        },
        "log_ring_size": {
            "description": "worker 日志环形缓冲条数",
            "default": 500,
            "value_type": ConfigValueType.INTEGER,
            "advanced": True,
        },
        "uv_exec": {
            "description": "uv 可执行文件路径覆盖（缺省自动探测）",
            "default": "",
            "advanced": True,
        },
        "pip_index_url": {
            "description": "worker venv 包安装自定义 PyPI 源（空 = 默认源）",
            "default": "",
            "advanced": True,
        },
        "pip_proxy": {
            "description": "包安装代理（空=继承系统；off=强制直连；其余如 http://127.0.0.1:7897）",
            "default": "",
            "advanced": True,
        },
        "python_exec": {
            "description": "创建 worker venv 所用 Python 覆盖（缺省用当前解释器）",
            "default": "",
            "advanced": True,
        },
    }
}


def get_default_channel_config() -> Dict[str, Any]:
    """返回 channel_config.json 的默认内容。"""
    return {
        "enabled": False,
        "adapters": [],
        "plugins": [],
        "nonebot_env": {},
        "intercept_all": False,
        "bridge_ws_port": 8197,
        "worker_host": "127.0.0.1",
        "worker_port": 8198,
        "auto_restart": True,
    }
