"""
实体系统 — 自动发现并加载所有实体模块。

每个实体是一个子目录，包含 ``tools.py`` 文件，
使用 ``@tool`` 装饰器和 ``entity()`` 声明注册到 ``EntityRegistry``。
导入本模块的 ``discover_entities()`` 即可触发全部实体注册。
``reload_entities()`` 支持运行时热重载。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Dict

from core.log import log

_loaded_modules: set[str] = set()


def discover_entities() -> list[str]:
    """扫描 entities/ 下所有子目录，导入 tools.py 触发 @tool 注册。

    返回成功加载的实体模块名列表。
    """
    entity_dir = Path(__file__).parent
    loaded: list[str] = []
    failed: list[str] = []

    for item in sorted(entity_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("_"):
            continue
        tools_file = item / "tools.py"
        if not tools_file.exists():
            continue
        module_path = f"entities.{item.name}.tools"
        try:
            importlib.import_module(module_path)
            loaded.append(item.name)
            _loaded_modules.add(item.name)
        except Exception as e:
            failed.append(item.name)
            log(f"entity load failed: {item.name} - {e}", "WARNING")

    if loaded:
        log(f"entities loaded: {', '.join(loaded)} ({len(loaded)})")
    if failed:
        log(f"entities failed: {', '.join(failed)} ({len(failed)})", "WARNING")
    return loaded


def reload_entities() -> Dict[str, Any]:
    """Hot-reload: rescan entities/ directory, load new ones, report status.

    Returns summary dict with added/existing/failed counts.
    """
    entity_dir = Path(__file__).parent
    added: list[str] = []
    existing: list[str] = []
    failed: list[str] = []

    for item in sorted(entity_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("_"):
            continue
        tools_file = item / "tools.py"
        if not tools_file.exists():
            continue

        module_path = f"entities.{item.name}.tools"

        if item.name in _loaded_modules:
            existing.append(item.name)
            try:
                mod = sys.modules.get(module_path)
                if mod:
                    importlib.reload(mod)
            except Exception as e:
                log(f"entity reload failed: {item.name} - {e}", "DEBUG")
            continue

        try:
            importlib.import_module(module_path)
            added.append(item.name)
            _loaded_modules.add(item.name)
            log(f"entity hot-loaded: {item.name}")
        except Exception as e:
            failed.append(item.name)
            log(f"entity hot-load failed: {item.name} - {e}", "WARNING")

    result = {
        "added": added,
        "existing": existing,
        "failed": failed,
        "total": len(_loaded_modules),
    }
    if added:
        log(f"hot-reload: {len(added)} new entities: {', '.join(added)}")
    return result
