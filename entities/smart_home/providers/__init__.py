"""智能家居平台连接包 — 目录自动发现（一个平台一个模块）。

新增平台 = 新建模块文件并定义 ``@home_provider`` 装饰的 SmartHomeProvider
子类；删除文件即整体拔出，无需改动其他任何文件。
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Dict, List, Optional

from core.log import log

from .base import SmartHomeProvider

_PROVIDERS: Dict[str, SmartHomeProvider] = {}


def home_provider(cls: type[SmartHomeProvider]) -> type[SmartHomeProvider]:
    """注册平台连接（实例化入注册表；同名覆盖）。"""
    if not cls.key:
        raise ValueError(f"{cls.__name__} 缺少 key 声明")
    _PROVIDERS[cls.key] = cls()
    return cls


def get_provider(key: str) -> Optional[SmartHomeProvider]:
    """按 key 取平台连接实例。"""
    return _PROVIDERS.get(key)


def all_providers() -> List[SmartHomeProvider]:
    """全部已注册平台连接。"""
    return list(_PROVIDERS.values())


def config_entries() -> Dict[str, Any]:
    """汇总全部平台的连接配置项（键已加前缀），供注册进 entity/smart_home 组。"""
    entries: Dict[str, Any] = {}
    for provider in all_providers():
        for name, item in provider.config_schema.items():
            entries[provider.full_config_key(name)] = dict(item)
    return entries


def load_providers() -> None:
    """扫描导入本包内全部平台模块（幂等）。"""
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        try:
            importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:
            log(f"智能家居平台 {info.name} 加载失败: {exc}", "WARNING", tag="智能家居")
