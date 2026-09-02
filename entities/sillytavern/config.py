"""SillyTavern 实体配置：读写 entities/sillytavern/config.json（进程级缓存）。

单一数据源，不注册 ConfigManager，经 /api/entity/sillytavern/config 读写，
与 AI 工具共用，避免双写不一致。
"""

from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict

from core.log import log

_DIR = os.path.dirname(os.path.abspath(__file__))
_CONFIG_FILE = os.path.join(_DIR, "config.json")
_lock = threading.Lock()
_cache: Dict[str, Any] | None = None

# 酒馆源码默认嵌套在实体目录下（独立 git 仓库，.gitignore 已忽略）
_DEFAULT_ST_DIR = os.path.join(_DIR, "SillyTavern")

_DEFAULTS: Dict[str, Any] = {
    "st_dir": _DEFAULT_ST_DIR,
    "port": 8000,
    "listen": False,          # False=仅本机 127.0.0.1，True=局域网可访问
    "disable_csrf": True,     # 程序化管理需要；本机监听场景风险可控
    "extra_args": [],         # 追加到 node server.js 的额外 CLI 参数
    "auto_start": False,      # AnelfAgent 启动时是否自动拉起酒馆
    "context_inject": True,   # 运行状态是否注入 AI 动态上下文
    "context_max_tokens": 300,
    "startup_timeout": 120,   # 启动后等待 /version 就绪的超时（秒），含首次 npm install
    "enable_bridge_plugin": True,  # 启动时注入 anelf-bridge 插件并开启 server plugins
}

_ALLOWED_KEYS = set(_DEFAULTS.keys())


def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(_DEFAULTS)
    for key, value in data.items():
        merged[key] = value
    return merged


def load_config() -> Dict[str, Any]:
    global _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            with open(_CONFIG_FILE, encoding="utf-8") as f:
                _cache = _merge_defaults(json.load(f))
        except FileNotFoundError:
            _cache = copy.deepcopy(_DEFAULTS)
        except Exception as e:
            log(f"SillyTavern 配置加载失败，使用默认值: {e}", "ERROR", tag="酒馆")
            _cache = copy.deepcopy(_DEFAULTS)
        return _cache


def save_config(data: Dict[str, Any]) -> Dict[str, Any]:
    global _cache
    unknown = [k for k in data if k not in _ALLOWED_KEYS]
    if unknown:
        raise ValueError(f"不支持的配置键: {', '.join(unknown)}（可选: {', '.join(sorted(_ALLOWED_KEYS))}）")
    cfg = _merge_defaults({**load_config(), **data})
    cfg["port"] = int(cfg["port"])
    cfg["startup_timeout"] = int(cfg["startup_timeout"])
    cfg["context_max_tokens"] = int(cfg["context_max_tokens"])
    with _lock:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        _cache = cfg
    return cfg


def st_dir() -> str:
    return str(load_config()["st_dir"])


def base_url() -> str:
    cfg = load_config()
    return f"http://127.0.0.1:{cfg['port']}"
