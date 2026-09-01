"""实体系统自省工具 — 查询实体目录和方法详情。"""

from __future__ import annotations

import json

from core.log import log
from entities._sdk import (
    ErrorCause,
    entity,
    error_from_exception,
    tool,
    tool_error,
    tool_group_rounds_left,
)

entity("entity", "实体系统自省 - 查询实体目录、方法详情和配置管理")


@tool(name="query_entities", concurrency_safe=True, group="entity", tags=["core"])
def query_entities(keyword: str = "", include_disabled: bool = False) -> str:
    """查询实体目录，默认只显示已启用的实体。

    Args:
        keyword: 关键词搜索（名称、描述、分组）
        include_disabled: 设为 true 可同时查看已禁用的实体/分组
    """
    try:
        from core.entity import EntityRegistry, EntityType

        if keyword:
            results = EntityRegistry.search(keyword)
            if not include_disabled:
                results = [e for e in results if e.enabled]
            return json.dumps({
                "keyword": keyword,
                "count": len(results),
                "results": [
                    {
                        "name": e.name,
                        "type": e.entity_type.value,
                        "description": e.description,
                        "enabled": e.enabled,
                        "group": e.group,
                    }
                    for e in results
                ],
            }, ensure_ascii=False)

        if include_disabled:
            groups: dict = {}
            for e in EntityRegistry.get_by_type(EntityType.TOOL):
                g = e.group or "default"
                if g not in groups:
                    groups[g] = {"group": g, "description": EntityRegistry.get_group_description(g),
                                 "tool_count": 0, "enabled_count": 0, "all_enabled": True}
                groups[g]["tool_count"] += 1
                if e.enabled:
                    groups[g]["enabled_count"] += 1
                else:
                    groups[g]["all_enabled"] = False
            catalog = sorted(groups.values(), key=lambda x: x["group"])
        else:
            catalog = EntityRegistry.get_entity_catalog()

        return json.dumps({
            "entity_count": len(catalog),
            "entities": catalog,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="查询实体目录")


@tool(name="list_entity_methods", concurrency_safe=True, group="entity", tags=["always"])
def list_entity_methods(group: str) -> str:
    """查看指定实体分组的所有可用方法及其参数详情。

    Args:
        group: 实体分组名称（如 os、environment、memory、web 等，完整列表见 query_entities 返回的目录）
    """
    try:
        import difflib

        from core.entity import EntityRegistry
        from core.entity import EntityType as ET

        entities = EntityRegistry.get_by_group(group)
        tools = [
            e for e in entities
            if e.entity_type == ET.TOOL and e.enabled
        ]
        if not tools:
            catalog = EntityRegistry.get_entity_catalog()
            available = [c["group"] for c in catalog]
            suggestions = difflib.get_close_matches(group, available, n=3, cutoff=0.5)
            return tool_error(f"实体分组 '{group}' 不存在或无可用方法",
                              cause=ErrorCause.NOT_FOUND, retryable=False,
                              group=group, available_groups=available,
                              did_you_mean=suggestions or None)

        description = EntityRegistry.get_group_description(group)
        methods = []
        for t in tools:
            params_info = []
            for p in t.meta.get("params", []):
                params_info.append({
                    "name": p.name,
                    "type": p.type,
                    "required": p.required,
                    "description": p.description,
                })
            methods.append({
                "name": t.name,
                "description": t.description,
                "params": params_info,
            })

        return json.dumps({
            "group": group,
            "description": description,
            **_sleep_state(group),
            "method_count": len(methods),
            "methods": methods,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="查询实体方法")


def _sleep_state(group: str) -> dict:
    """可沉睡分组的当前激活状态标注（非可沉睡分组返回空）。

    沉睡中的分组方法未注入工具列表，AI 无法直接调用——必须显式告知，
    否则 AI 会误以为方法可用而在"发现→调用失败→再发现"间死循环。
    """
    from core.entity import EntityRegistry

    if group not in EntityRegistry.get_sleepable_groups():
        return {}
    rounds_left = tool_group_rounds_left(group)
    if rounds_left > 0:
        return {"sleeping": False, "active_rounds_left": rounds_left}
    return {
        "sleeping": True,
        "hint": f"该分组当前处于沉睡状态，以下方法未注入工具列表、无法直接调用。"
                f"请先调用 activate_tool_group(group=\"{group}\") 唤醒，"
                f"激活后即可直接调用这些方法。",
    }


@tool(name="get_entity_config", group="entity", tags=["core"], concurrency_safe=True)
def get_entity_config(entity_name: str) -> str:
    """查看指定实体的配置信息，包括当前值、默认值和配置描述。

    Args:
        entity_name: 实体名称或关键词（支持模糊匹配）
    """
    try:
        from core.entity import EntityRegistry

        metadata = EntityRegistry.get(entity_name)
        if metadata is None:
            results = EntityRegistry.search(entity_name)
            with_config = [e for e in results if e.get_config_items()]
            if not with_config:
                return tool_error(f"未找到实体 '{entity_name}' 或该实体无配置",
                                  cause=ErrorCause.NOT_FOUND, retryable=False,
                                  hint="可用 query_entities 搜索可用实体")
            metadata = with_config[0]

        config_items = metadata.get_config_items()
        return json.dumps({
            "entity": metadata.name,
            "type": metadata.entity_type.value,
            "config_group": metadata.config_group,
            "config_items": config_items,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="获取实体配置")


@tool(name="update_entity_config", group="entity", tags=["core"])
def update_entity_config(key: str, value: str) -> str:
    """修改实体的配置项。

    Args:
        key: 配置项键名
        value: 新的配置值（字符串形式，会自动转换类型）
    """
    try:
        from core.config import ConfigManager, ConfigRegistry

        item = ConfigRegistry.get_item(key)
        if item is None:
            return tool_error(f"配置项 '{key}' 不存在", cause=ErrorCause.NOT_FOUND,
                              retryable=False,
                              hint="可用 get_entity_config 查看实体的可用配置项")

        parsed_value: object = value
        try:
            parsed_value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            log("update_entity_config 异常已忽略", "DEBUG")

        ConfigManager.set(key, parsed_value)
        ConfigManager.save()

        return json.dumps({
            "success": True,
            "key": key,
            "new_value": parsed_value,
            "description": item.description,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="更新实体配置")


@tool(name="get_entity_status", concurrency_safe=True, group="entity", tags=["core"])
def get_entity_status(entity_name: str = "") -> str:
    """查看实体系统整体状态或指定实体的详细状态。

    Args:
        entity_name: 实体名称（为空返回整体统计）
    """
    try:
        from core.entity import EntityRegistry

        if not entity_name:
            stats = EntityRegistry.get_statistics()
            return json.dumps(stats, ensure_ascii=False)

        metadata = EntityRegistry.get(entity_name)
        if metadata is None:
            results = EntityRegistry.search(entity_name)
            if not results:
                return tool_error(f"未找到实体 '{entity_name}'",
                                  cause=ErrorCause.NOT_FOUND, retryable=False,
                                  hint="可用 query_entities 搜索可用实体")
            metadata = results[0]

        info = {
            "name": metadata.name,
            "type": metadata.entity_type.value,
            "description": metadata.description,
            "enabled": metadata.enabled,
            "group": metadata.group,
            "source": metadata.source,
            "tags": metadata.tags,
            "config_group": metadata.config_group,
            "has_instance": metadata.instance is not None,
            "apis": metadata.get_registered_apis(),
        }
        if configs := metadata.get_all_configs():
            info["configs"] = configs

        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="查询实体状态")


@tool(name="toggle_entity_group", group="entity", tags=["core"])
def toggle_entity_group(group: str, enabled: bool = True) -> str:
    """启用或禁用实体分组内的所有工具（与 Web 开关同路径，状态持久化，重启后保持）。

    Args:
        group: 实体分组名称（如 os、environment、web）
        enabled: true 启用，false 禁用
    """
    try:
        from core.entity import EntityRegistry

        count = EntityRegistry.set_group_enabled(group, enabled)

        return json.dumps({
            "success": True,
            "group": group,
            "enabled": enabled,
            "affected_count": count,
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="切换实体分组状态")
