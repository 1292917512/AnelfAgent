"""wcauto 桥配置 — JSON 文件 + 环境变量覆盖，纯标准库。

配置优先级：命令行参数 > 环境变量（WCAUTO_BRIDGE_*）> JSON 文件 > 默认值。
token 也可经环境变量 WCAUTO_BRIDGE_TOKEN 注入（不落盘场景）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

ENV_PREFIX = "WCAUTO_BRIDGE_"


@dataclass
class BridgeConfig:
    """桥服务配置。"""

    host: str = "0.0.0.0"
    port: int = 8655
    token: str = ""
    # 微信账号 wxid；留空自动检测（取最近活跃的账号）
    account: str = ""
    # 不监听这些会话（username 或昵称，逗号分隔在 JSON 里用数组）
    ignore_chats: List[str] = field(default_factory=list)
    # 单条入站媒体回传上限（MB），超出返回 too_large
    media_max_mb: int = 25
    # 事件环形缓冲容量（断线重放用）
    event_buffer: int = 2000
    # 防撤回镜像（RecallGuard）：镜像库/媒体备份目录，留空用 wechatauto 默认
    enable_recall_guard: bool = True
    recall_mirror_dir: str = ""
    # 朋友圈读取（MomentDB，只读；点赞/评论需窗口可见）
    enable_moments: bool = True
    # 桥数据目录（下载媒体缓存等），留空为 <配置目录>/data
    data_dir: str = ""
    # 微信 4.x 数据目录（xwechat_files 所在地）；留空自动探测，跨用户会话/自定义路径时须显式指定
    db_dir: str = ""

    @property
    def media_max_bytes(self) -> int:
        return max(int(self.media_max_mb), 1) * 1024 * 1024


def load_config(path: str = "") -> BridgeConfig:
    """加载配置：JSON 文件 → 环境变量覆盖。"""
    values: Dict[str, Any] = {}
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError(f"桥配置文件须为 JSON 对象: {path}")
        values = raw

    cfg = BridgeConfig()
    for key in cfg.__dataclass_fields__:
        if key in values:
            setattr(cfg, key, values[key])
        env_key = ENV_PREFIX + key.upper()
        if env_key in os.environ:
            setattr(cfg, key, _coerce(os.environ[env_key], key))
    cfg.port = int(cfg.port)
    cfg.ignore_chats = [str(x).strip() for x in (cfg.ignore_chats or []) if str(x).strip()]
    return cfg


def _coerce(raw: str, key: str) -> Any:
    """环境变量字符串按字段默认类型转换。"""
    default = getattr(BridgeConfig(), key)
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, list):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return raw
