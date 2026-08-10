"""Web 实体本地配置（entities/web/config.json）。

存储网页抓取代理等本地设置，进程级缓存，更新时持久化并刷新缓存。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from core.log import log

_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

_config_cache: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    """加载配置文件（进程级缓存）。"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            _config_cache = json.load(f)
    except Exception as e:
        log(f"加载 Web 工具配置失败: {e}", "ERROR")
        _config_cache = {}
    return _config_cache


def get_config() -> Dict[str, Any]:
    """获取完整配置（返回副本）。"""
    return dict(_load_config())


def get_proxy() -> str:
    """获取代理地址（空字符串表示不使用代理）。"""
    return _load_config().get("proxy", "")


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """更新配置并持久化到文件，返回更新后的完整配置。"""
    global _config_cache
    current = dict(_load_config())
    current.update(updates)
    try:
        with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=4)
        _config_cache = current
        log("Web 工具配置已更新", tag="Web")
    except Exception as e:
        log(f"保存 Web 工具配置失败: {e}", "ERROR")
        raise
    return dict(current)


def reload_config() -> None:
    """强制重新加载配置（清除缓存）。"""
    global _config_cache
    _config_cache = None
