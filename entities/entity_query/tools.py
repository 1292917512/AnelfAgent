"""实体系统自省工具 — 查询实体目录和方法详情。"""

from __future__ import annotations

import json

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


def _serialize_config_items(group: str) -> list:
    """序列化指定配置组的配置项（PASSWORD 类型值掩码，密钥不出上下文）。"""
    from core.config import ConfigManager, ConfigRegistry, mask_secret

    items = []
    for item in ConfigRegistry.get_group_items(group):
        value = ConfigManager.get(item.key, item.default_value)
        if item.is_secret and isinstance(value, str) and value:
            value = mask_secret(value)
        items.append({
            "key": item.key,
            "description": item.description,
            "value": value,
            "default": item.default_value,
            "type": item.type_name,
            "editable": item.editable,
            "required": item.required,
            "enum_options": item.enum_options,
        })
    return items


@tool(name="list_config_groups", group="entity", tags=["core"], concurrency_safe=True)
def list_config_groups() -> str:
    """列出所有配置分组（频道 adapter/<id>、实体 entity/<name>、系统模块），供按分类浏览配置。"""
    try:
        from core.config import ConfigRegistry

        groups = [
            {"group": g, "item_count": len(ConfigRegistry.get_group_items(g))}
            for g in sorted(ConfigRegistry.get_all_groups())
        ]
        return json.dumps({
            "groups": groups,
            "hint": "用 get_entity_config 查看某频道/实体的配置详情（如 get_entity_config(\"qq\")），"
                    "用 update_entity_config 修改配置项",
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="列出配置分组")


@tool(name="get_entity_config", group="entity", tags=["core"], concurrency_safe=True)
def get_entity_config(entity_name: str) -> str:
    """查看指定实体或频道的配置信息，包括当前值、默认值和配置描述。

    Args:
        entity_name: 实体/频道名称或关键词（支持模糊匹配，如 qq、telegram、ssh、web）
    """
    try:
        from core.config import ConfigRegistry
        from core.entity import EntityRegistry

        # 配置组直查（适配器可能未启用而无实体实例，组却已有 schema）
        for candidate in (entity_name, f"adapter/{entity_name}", f"entity/{entity_name}"):
            if candidate in ConfigRegistry.get_all_groups():
                return json.dumps({
                    "entity": entity_name,
                    "config_group": candidate,
                    "config_items": _serialize_config_items(candidate),
                }, ensure_ascii=False)

        metadata = EntityRegistry.get(entity_name)
        if metadata is None:
            results = EntityRegistry.search(entity_name)
            with_config = [e for e in results if e.config_group]
            if not with_config:
                return tool_error(f"未找到实体 '{entity_name}' 或该实体无配置",
                                  cause=ErrorCause.NOT_FOUND, retryable=False,
                                  hint="可用 list_config_groups 按分类浏览全部配置")
            metadata = with_config[0]

        return json.dumps({
            "entity": metadata.name,
            "type": metadata.entity_type.value,
            "config_group": metadata.config_group,
            "config_items": _serialize_config_items(metadata.config_group),
        }, ensure_ascii=False)
    except Exception as e:
        return error_from_exception(e, action="获取实体配置")


@tool(name="update_entity_config", group="entity", tags=["core"], risk="CRITICAL")
def update_entity_config(key: str, value: str) -> str:
    """修改配置项（实体/频道/系统统一入口，立即热更生效并持久化）。

    Args:
        key: 配置项键名（经 get_entity_config / list_config_groups 获取，如 qq_group_whitelist）
        value: 新的配置值（按声明类型自动转换并校验）
    """
    try:
        from core.config import ConfigRegistry
        from entities._sdk import save_config_value

        item = ConfigRegistry.get_item(key)
        if item is None:
            return tool_error(f"配置项 '{key}' 不存在", cause=ErrorCause.NOT_FOUND,
                              retryable=False,
                              hint="可用 list_config_groups / get_entity_config 查看可用配置项")
        if not item.editable:
            return tool_error(f"配置项 '{key}' 不可编辑", cause=ErrorCause.PERMISSION,
                              retryable=False)

        try:
            parsed = item.clamp(item.coerce_value(value))
        except ValueError as exc:
            return tool_error(str(exc), cause=ErrorCause.PARAM, retryable=False)
        if item.type_name == "enum" and item.enum_options and parsed not in item.enum_options:
            return tool_error(
                f"配置项 '{key}' 的值必须是 {item.enum_options} 之一",
                cause=ErrorCause.PARAM, retryable=False)

        save_config_value(key, parsed)

        return json.dumps({
            "success": True,
            "key": key,
            "new_value": parsed,
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
