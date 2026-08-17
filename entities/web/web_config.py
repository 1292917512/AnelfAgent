"""Web 实体本地配置（entities/web/config.json）。

存储抓取代理、能力 × 提供者矩阵选择（active）、提供者启停（disabled）、
提供者凭据（provider_keys）等本地设置，进程级缓存，更新时持久化并刷新缓存。

配置结构：
{
    "proxy": "",
    "active": {"search": "auto", "reader": "auto", "repo": "auto"},
    "disabled": ["bigmodel"],
    "provider_keys": {"bigmodel": "..."}
}
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

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
    except FileNotFoundError:
        _config_cache = {}
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


def get_active(capability: str) -> str:
    """指定能力配置的固定提供者（auto 表示自动选择）。"""
    active = _load_config().get("active")
    if isinstance(active, dict):
        return str(active.get(capability, "auto")) or "auto"
    return "auto"


def set_active(capability: str, name: str) -> None:
    """设置指定能力的固定提供者（auto 恢复自动选择）。"""
    active = _load_config().get("active")
    merged = dict(active) if isinstance(active, dict) else {}
    merged[capability] = name.strip() or "auto"
    update_config({"active": merged})


def is_enabled(provider: str) -> bool:
    """提供者启用状态（默认启用，disabled 列表中的为禁用）。"""
    disabled = _load_config().get("disabled")
    return not (isinstance(disabled, list) and provider in disabled)


def set_enabled(provider: str, enabled: bool) -> None:
    """设置提供者启用状态。"""
    current = _load_config().get("disabled")
    disabled: List[str] = list(current) if isinstance(current, list) else []
    if enabled:
        disabled = [name for name in disabled if name != provider]
    elif provider not in disabled:
        disabled.append(provider)
    update_config({"disabled": disabled})


def get_provider_key(name: str) -> str:
    """获取指定提供者持久化的 API Key（无则空串）。"""
    keys = _load_config().get("provider_keys")
    return str(keys.get(name, "")).strip() if isinstance(keys, dict) else ""


def set_provider_key(name: str, api_key: str) -> None:
    """持久化提供者 API Key（空串表示清除）。"""
    current = _load_config().get("provider_keys")
    keys = dict(current) if isinstance(current, dict) else {}
    key = api_key.strip()
    if key:
        keys[name] = key
    else:
        keys.pop(name, None)
    update_config({"provider_keys": keys})


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
