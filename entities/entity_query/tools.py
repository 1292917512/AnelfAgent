"""实体系统自省工具 — 查询实体目录和方法详情。"""

from __future__ import annotations

import json

from entities._sdk import tool, entity

entity("entity", "实体系统自省 - 查询实体目录、方法详情和配置管理")


@tool(name="query_entities", group="entity", tags=["core"])
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
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="list_entity_methods", group="entity", tags=["always"])
def list_entity_methods(group: str) -> str:
    """查看指定实体分组的所有可用方法及其参数详情。

    Args:
        group: 实体分组名称（如 filesystem、system、git 等）
    """
    try:
        from core.entity import EntityRegistry, EntityType as ET

        entities = EntityRegistry.get_by_group(group)
        tools = [
            e for e in entities
            if e.entity_type == ET.TOOL and e.enabled
        ]
        if not tools:
            return json.dumps({
                "group": group,
                "error": f"实体分组 '{group}' 不存在或无可用方法",
            }, ensure_ascii=False)

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
            "method_count": len(methods),
            "methods": methods,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="get_entity_config", group="entity", tags=["core"])
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
            with_config = [e for e in results if e.config_group]
            if not with_config:
                return json.dumps({
                    "error": f"未找到实体 '{entity_name}' 或该实体无配置",
                    "hint": "可用 query_entities 搜索可用实体",
                }, ensure_ascii=False)
            metadata = with_config[0]

        config_items = metadata.get_config_items()
        return json.dumps({
            "entity": metadata.name,
            "type": metadata.entity_type.value,
            "config_group": metadata.config_group,
            "config_items": config_items,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


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
            return json.dumps({
                "error": f"配置项 '{key}' 不存在",
            }, ensure_ascii=False)

        parsed_value: object = value
        try:
            parsed_value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass

        ConfigManager.set(key, parsed_value)
        ConfigManager.save()

        return json.dumps({
            "success": True,
            "key": key,
            "new_value": parsed_value,
            "description": item.description,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="get_entity_status", group="entity", tags=["core"])
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
                return json.dumps({
                    "error": f"未找到实体 '{entity_name}'",
                }, ensure_ascii=False)
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
        if metadata.config_group:
            info["configs"] = metadata.get_all_configs()

        return json.dumps(info, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@tool(name="toggle_entity_group", group="entity", tags=["core"])
def toggle_entity_group(group: str, enabled: bool = True) -> str:
    """启用或禁用实体分组内的所有工具。

    Args:
        group: 实体分组名称（如 filesystem、system、web）
        enabled: true 启用，false 禁用
    """
    try:
        from core.entity import EntityRegistry

        if enabled:
            count = EntityRegistry.enable_group(group)
        else:
            count = EntityRegistry.disable_group(group)

        return json.dumps({
            "success": True,
            "group": group,
            "enabled": enabled,
            "affected_count": count,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
