"""外部技能源注册表 — 热插拔入口。

拔插约定：`_SOURCE_MODULES` 登记的模块导入失败（文件被删除 / 依赖缺失）时
跳过并记 WARNING，技能系统核心不受任何影响；删除对应 .py 文件即完成卸载，
重新放入文件并重启即完成安装。
"""
from __future__ import annotations

import importlib
import threading
from typing import Dict, List, Optional

from agent.skills.sources.base import ExternalSkill, InstallResult, SkillSource
from core.log import log

__all__ = [
    "ExternalSkill", "InstallResult", "SkillSource",
    "list_sources", "get_source", "reset_sources",
]

# 已接入的外部技能源模块名（本包内）
_SOURCE_MODULES = ("skillhub",)

_lock = threading.Lock()
_sources: Optional[Dict[str, SkillSource]] = None


def _ensure_loaded() -> Dict[str, SkillSource]:
    existing = _sources
    if existing is not None:
        return existing
    with _lock:
        return _load_locked()


def _load_locked() -> Dict[str, SkillSource]:
    """在锁内完成首次加载（调用方须持有 _lock）。"""
    global _sources
    existing = _sources
    if existing is not None:
        return existing
    loaded: Dict[str, SkillSource] = {}
    for module_name in _SOURCE_MODULES:
        try:
            module = importlib.import_module(f".{module_name}", __package__)
            source = module.get_source()
        except Exception as exc:
            log(f"外部技能源加载失败（已跳过）: {module_name}: {exc}", "WARNING", tag="技能")
            continue
        if isinstance(source, SkillSource) and source.key:
            loaded[source.key] = source
        else:
            log(f"外部技能源无效（已跳过）: {module_name}", "WARNING", tag="技能")
    _sources = loaded
    log(f"🔌 外部技能源: {', '.join(loaded) if loaded else '无'}", "DEBUG", tag="技能")
    return _sources


def list_sources() -> List[SkillSource]:
    """列出全部已加载的外部技能源。"""
    return list(_ensure_loaded().values())


def get_source(key: str) -> Optional[SkillSource]:
    """按 key 获取外部技能源。"""
    return _ensure_loaded().get(key)


def reset_sources() -> None:
    """重置注册表缓存（测试用）。"""
    global _sources
    with _lock:
        _sources = None
