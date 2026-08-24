"""启动状态恢复 — 持久化覆盖/启停/自定义标签的回放。

三个实现均为纯 core 操作（ConfigManager + EntityRegistry + core.tags），
由 bootstrap 的 restore_states 节点在启动早期调用；services 层同名方法
委托于此（web 侧读写路径复用同一实现）。
"""

from __future__ import annotations

from typing import Any, Dict

from core.log import log


def apply_tool_overrides() -> int:
    """启动时加载持久化的工具属性覆盖，返回应用的覆盖数量。"""
    from core.config import ConfigManager
    from core.entity import EntityRegistry

    overrides: dict = ConfigManager.get("tool_overrides", {})
    if not isinstance(overrides, dict) or not overrides:
        return 0

    applied = 0
    for name, meta in overrides.items():
        entity = EntityRegistry.get(name)
        if entity is None:
            continue
        if "tags" in meta and isinstance(meta["tags"], list):
            entity.tags = meta["tags"]
        if "description" in meta and isinstance(meta["description"], str):
            entity.description = meta["description"]
        applied += 1

    if applied:
        # 直接修改了实体元数据，递增注册表版本使派生缓存失效
        EntityRegistry.bump_version()
        log(f"工具属性覆盖已加载: {applied} 个工具", tag="工具")
    return applied


def apply_entity_states() -> int:
    """启动时从 app_config.json 恢复实体启用/禁用状态，返回应用数量。"""
    from core.config import ConfigManager
    from core.entity import EntityRegistry

    states: dict = ConfigManager.get("entity_states", {})
    if not isinstance(states, dict) or not states:
        return 0

    applied = 0
    for name, enabled in states.items():
        if not isinstance(enabled, bool):
            continue
        if not EntityRegistry.exists(name):
            continue
        if enabled:
            EntityRegistry.enable(name)
        else:
            EntityRegistry.disable(name)
        applied += 1

    if applied:
        log(f"实体状态已恢复: {applied} 个实体", tag="实体")
    return applied


def load_tags_file() -> Dict[str, Any]:
    """读取 config/tags.json，不存在则返回空字典。"""
    import json
    from pathlib import Path

    from core.path import ConfigPaths

    p = Path(ConfigPaths.CUSTOM_TAGS)
    if p.exists():
        try:
            return json.loads(p.read_text("utf-8"))
        except Exception as e:
            log(f"读取标签配置失败: {e}", "ERROR", tag="Tags")
    return {}


def load_custom_tags() -> int:
    """从 config/tags.json 加载自定义标签到内存 tag_list（幂等，首次调用时触发）。"""
    from core.tags import Tag, tag_list

    data = load_tags_file()
    if not data:
        return 0

    existing_names = {t.tag_name for t in tag_list}
    loaded = 0
    for name, meta in data.items():
        if name in existing_names:
            continue
        Tag(tag_name=name, tag_name_desc=meta.get("description", ""))
        loaded += 1

    if loaded:
        log(f"自定义标签已加载: {loaded} 个", tag="Tags")
    return loaded
