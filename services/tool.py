"""工具管理服务 -- 工具列表、分组管理、启禁用、属性编辑、热重载、插件列表。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.log import log


def _group_sort_key(group: str) -> tuple:
    """分组排序键：统一读取 EntityRegistry 权重（实体经 manifest order 自声明）。

    未注册权重的分组（含 mcp:*）按字母序排在末尾，与 LLM 工具目录同口径。
    """
    from core.entity import EntityRegistry
    return EntityRegistry.group_sort_key(group)


class ToolService:

    def list_tools(self) -> List[Dict[str, Any]]:
        """返回已注册工具扁平列表。"""
        from core.entity import EntityRegistry, EntityType
        return [
            {
                "name": e.name,
                "source": e.source,
                "group": e.group,
                "enabled": e.enabled,
                "description": e.description,
                "tags": list(e.tags),
            }
            for e in EntityRegistry.get_by_type(EntityType.TOOL)
        ]

    def list_grouped_tools(self) -> List[Dict[str, Any]]:
        """返回按实体分组的工具列表（含分组描述和启用状态）。"""
        from core.entity import EntityRegistry, EntityType

        groups: Dict[str, Dict[str, Any]] = {}
        for e in EntityRegistry.get_by_type(EntityType.TOOL):
            g = e.group or "default"
            if g not in groups:
                groups[g] = {
                    "group": g,
                    "description": EntityRegistry.get_group_description(g),
                    "tools": [],
                    "enabled_count": 0,
                    "total_count": 0,
                }
            groups[g]["tools"].append({
                "name": e.name,
                "source": e.source,
                "enabled": e.enabled,
                "description": e.description,
                "tags": list(e.tags),
            })
            groups[g]["total_count"] += 1
            if e.enabled:
                groups[g]["enabled_count"] += 1

        result = list(groups.values())
        for g in result:
            g["all_enabled"] = g["enabled_count"] == g["total_count"] and g["total_count"] > 0
            g["any_enabled"] = g["enabled_count"] > 0
        return sorted(result, key=lambda x: _group_sort_key(x["group"]))

    def toggle_tool(self, name: str) -> bool:
        """切换工具启用/禁用状态，并持久化。返回切换后是否启用。"""
        from core.entity import EntityRegistry

        e = EntityRegistry.get(name)
        if e is None:
            raise ValueError(f"tool '{name}' not found")
        new_state = not e.enabled
        EntityRegistry.set_enabled(name, new_state)
        return new_state

    def toggle_group(self, group: str) -> bool:
        """切换整个分组的启用/禁用状态，并持久化。返回切换后是否启用。"""
        from core.entity import EntityRegistry

        new_enabled = not EntityRegistry.is_group_enabled(group)
        EntityRegistry.set_group_enabled(group, new_enabled)
        return new_enabled

    def update_tool_meta(
        self,
        name: str,
        tags: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> bool:
        """修改工具的 tags 和 description，并持久化到 app_config.json。

        tags 必须全部来自标签系统（core/tags.tag_list），不允许使用未注册的标签。
        """
        from core.config import ConfigManager
        from core.entity import EntityRegistry
        from services.tag import TagService

        entity = EntityRegistry.get(name)
        if entity is None:
            return False

        if tags is not None:
            invalid = TagService.validate_tags(tags)
            if invalid:
                raise ValueError(f"以下标签不在标签系统中: {', '.join(invalid)}")
            entity.tags = tags
        if description is not None:
            entity.description = description
        # 直接修改了实体元数据，递增注册表版本使派生缓存（标签索引/schema）失效
        EntityRegistry.bump_version()

        # 持久化覆盖到 ConfigManager
        overrides: dict = ConfigManager.get("tool_overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}

        entry: Dict[str, Any] = overrides.get(name, {})
        if tags is not None:
            entry["tags"] = tags
        if description is not None:
            entry["description"] = description
        overrides[name] = entry

        ConfigManager.set("tool_overrides", overrides)
        ConfigManager.save()
        log(f"工具属性已更新并持久化: {name} tags={tags} desc={description[:30] if description else None}", tag="工具")
        return True

    @staticmethod
    def apply_overrides() -> int:
        """启动时加载持久化的工具属性覆盖（实现归 agent.runtime.state_restore）。"""
        from agent.runtime.state_restore import apply_tool_overrides
        return apply_tool_overrides()

    async def reload_entities(self) -> Dict[str, Any]:
        """热同步实体：对账 entities/ 目录（新增热插入 / 消失热拔除 / 存续热重载）。"""
        from entities import reload_entities
        return await reload_entities(reload_existing=True)

    def list_plugins(self) -> List[Dict[str, Any]]:
        """返回已安装插件列表（管理面归 entities/plugins 实体）。"""
        from core.plugins import get_plugin_manager
        return [p.to_dict() for p in get_plugin_manager().list_plugins()]
