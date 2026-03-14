"""实体管理服务 -- 查询实体、读写配置、启禁用。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.log import log


class EntityService:
    """实体系统业务逻辑层。"""

    def list_entities(
        self,
        entity_type: Optional[str] = None,
        group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出所有已注册实体，可按类型或分组过滤。"""
        from core.entity import EntityRegistry, EntityType

        if entity_type:
            try:
                et = EntityType(entity_type)
            except ValueError:
                return []
            entities = EntityRegistry.get_by_type(et)
        elif group:
            entities = EntityRegistry.get_by_group(group)
        else:
            entities = EntityRegistry.get_all()

        return [
            {
                "name": e.name,
                "type": e.entity_type.value,
                "description": e.description,
                "enabled": e.enabled,
                "group": e.group,
                "source": e.source,
                "tags": e.tags,
                "config_group": e.config_group,
                "has_instance": e.instance is not None,
            }
            for e in entities
        ]

    def get_entity_detail(self, name: str) -> Optional[Dict[str, Any]]:
        """获取实体详情（含配置和 API 列表）。"""
        from core.entity import EntityRegistry

        metadata = EntityRegistry.get(name)
        if metadata is None:
            return None

        detail: Dict[str, Any] = {
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
            "config_items": metadata.get_config_items(),
            "configs": metadata.get_all_configs(),
        }
        return detail

    def get_entity_config(self, name: str) -> Optional[Dict[str, Any]]:
        """获取实体配置。"""
        from core.entity import EntityRegistry

        metadata = EntityRegistry.get(name)
        if metadata is None:
            return None

        return {
            "config_group": metadata.config_group,
            "items": metadata.get_config_items(),
            "values": metadata.get_all_configs(),
        }

    def update_entity_config(self, name: str, key: str, value: Any) -> bool:
        """更新实体配置项。"""
        from core.config import ConfigManager, ConfigRegistry
        from core.entity import EntityRegistry

        metadata = EntityRegistry.get(name)
        if metadata is None:
            return False

        item = ConfigRegistry.get_item(key)
        if item is None:
            return False

        ConfigManager.set(key, value)
        ConfigManager.save()
        return True

    def set_entity_enabled(self, name: str, enabled: bool) -> bool:
        """启用/禁用实体，并持久化到 app_config.json。"""
        from core.entity import EntityRegistry
        from core.config import ConfigManager

        if not EntityRegistry.exists(name):
            return False
        result = EntityRegistry.enable(name) if enabled else EntityRegistry.disable(name)

        states: dict = ConfigManager.get("entity_states", {})
        if not isinstance(states, dict):
            states = {}
        states[name] = enabled
        ConfigManager.set("entity_states", states)
        ConfigManager.save()

        return result

    @staticmethod
    def apply_entity_states() -> int:
        """启动时从 app_config.json 恢复实体启用/禁用状态，返回应用数量。"""
        from core.entity import EntityRegistry
        from core.config import ConfigManager

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

    def get_statistics(self) -> Dict[str, Any]:
        """获取实体统计。"""
        from core.entity import EntityRegistry
        return EntityRegistry.get_statistics()

    def get_catalog(self) -> List[Dict[str, Any]]:
        """获取实体目录（两级发现的第一级）。"""
        from core.entity import EntityRegistry
        return EntityRegistry.get_entity_catalog()
