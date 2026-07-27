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
                "manifest": EntityRegistry.get_group_manifest(e.group),
            }
            for e in entities
        ]

    def get_entity_detail(self, name: str) -> Optional[Dict[str, Any]]:
        """获取实体详情（含配置、API、工具列表和上下文提供者）。"""
        from core.entity import EntityRegistry, EntityType

        metadata = EntityRegistry.get(name)
        if metadata is None:
            return None

        group = metadata.group

        # 该分组下的工具列表
        group_tools = []
        for e in EntityRegistry.get_by_group(group):
            if e.entity_type == EntityType.TOOL:
                group_tools.append({
                    "name": e.name,
                    "enabled": e.enabled,
                    "description": e.description,
                })

        # 该实体注册的上下文提供者
        providers = []
        try:
            from core.context_provider import ContextProviderRegistry
            for p in ContextProviderRegistry.get_all():
                if p.scope_filter is None or p.scope_filter.startswith(group):
                    providers.append({
                        "name": p.name,
                        "priority": p.priority,
                        "max_tokens": p.max_tokens,
                        "description": p.description,
                    })
        except Exception:
            pass

        detail: Dict[str, Any] = {
            "name": metadata.name,
            "type": metadata.entity_type.value,
            "description": metadata.description,
            "enabled": metadata.enabled,
            "group": group,
            "source": metadata.source,
            "tags": metadata.tags,
            "config_group": metadata.config_group,
            "has_instance": metadata.instance is not None,
            "apis": metadata.get_registered_apis(),
            "config_items": metadata.get_config_items(),
            "configs": metadata.get_all_configs(),
            "manifest": EntityRegistry.get_group_manifest(group),
            "tools": group_tools,
            "providers": providers,
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

        # 同步写入实体目录的 config.json（如果存在）
        self._sync_entity_config_file(metadata.group, key, value)
        return True

    def update_entity_config_batch(
        self, name: str, updates: Dict[str, Any],
    ) -> int:
        """批量更新实体配置项，返回成功更新数量。"""
        from core.config import ConfigManager, ConfigRegistry
        from core.entity import EntityRegistry

        metadata = EntityRegistry.get(name)
        if metadata is None:
            return 0

        count = 0
        for key, value in updates.items():
            item = ConfigRegistry.get_item(key)
            if item is None:
                continue
            ConfigManager.set(key, value)
            count += 1

        if count:
            ConfigManager.save()
            # 批量同步到 config.json
            self._sync_entity_config_file_batch(metadata.group, updates)

        return count

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

    # ------------------------------------------------------------------
    # 实体目录 config.json 同步
    # ------------------------------------------------------------------

    @staticmethod
    def _find_entity_dir(group: str) -> Optional[str]:
        """根据 group 名查找实体目录。"""
        import os
        from pathlib import Path
        entities_dir = Path(__file__).parent.parent / "entities"
        candidate = entities_dir / group
        if candidate.is_dir():
            return str(candidate)
        return None

    def _sync_entity_config_file(
        self, group: str, key: str, value: Any,
    ) -> None:
        """将单个配置项同步写入实体目录的 config.json。"""
        entity_dir = self._find_entity_dir(group)
        if not entity_dir:
            return
        self._write_config_json(entity_dir, {key: value})

    def _sync_entity_config_file_batch(
        self, group: str, updates: Dict[str, Any],
    ) -> None:
        """将批量配置项同步写入实体目录的 config.json。"""
        entity_dir = self._find_entity_dir(group)
        if not entity_dir:
            return
        self._write_config_json(entity_dir, updates)

    @staticmethod
    def _write_config_json(entity_dir: str, updates: Dict[str, Any]) -> None:
        """合并写入 config.json（读-改-写）。"""
        import json
        import os

        config_path = os.path.join(entity_dir, "config.json")
        existing: Dict[str, Any] = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}

        existing.update(updates)
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            log(f"实体配置写入失败: {config_path} - {exc}", "DEBUG", tag="实体")
