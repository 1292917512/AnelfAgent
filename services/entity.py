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

        return sorted(
            (
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
            ),
            # 按分组排序权重归类（一类的放一块），同组内按实体名稳定
            key=lambda item: (*EntityRegistry.group_sort_key(item["group"]), item["name"]),
        )

    @staticmethod
    def _resolve_metadata(name: str) -> Optional[Any]:
        """按实体名或分组名解析实体元数据（与 get_entity_detail 同口径）。

        分组实体（工具以各自名称注册、组名下无同名实体）按分组名
        取该组第一个实体，保证配置读写对全部分组实体可用。
        """
        from core.entity import EntityRegistry

        metadata = EntityRegistry.get(name)
        if metadata is None:
            group_entities = EntityRegistry.get_by_group(name)
            if group_entities:
                metadata = group_entities[0]
        return metadata

    def get_entity_detail(self, name: str) -> Optional[Dict[str, Any]]:
        """获取实体详情（含配置、API、工具列表和上下文提供者）。

        支持按实体名或分组名查找：先按实体名精确匹配，
        未命中时按分组名查找该组下第一个实体。
        """
        from core.entity import EntityRegistry, EntityType

        metadata = self._resolve_metadata(name)
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
            log("get_entity_detail 异常已忽略", "DEBUG")

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
        metadata = self._resolve_metadata(name)
        if metadata is None:
            return None

        return {
            "config_group": metadata.config_group,
            "items": metadata.get_config_items(),
            "values": metadata.get_all_configs(),
        }

    @staticmethod
    def _coerce_item_value(key: str, value: Any) -> Any:
        """按配置项声明类型矫正并收敛值（与配置中心 PUT 同纪律）。

        配置项不存在或类型非法时抛 ValueError，调用方据此判失败。
        """
        from core.config import ConfigRegistry

        item = ConfigRegistry.get_item(key)
        if item is None:
            raise ValueError(f"配置项不存在: {key}")
        return item.clamp(item.coerce_value(value))

    def update_entity_config(self, name: str, key: str, value: Any) -> bool:
        """更新实体配置项（类型矫正 + 边界收敛后持久化）。"""
        from core.config import ConfigManager

        metadata = self._resolve_metadata(name)
        if metadata is None:
            return False

        try:
            coerced = self._coerce_item_value(key, value)
        except ValueError:
            return False

        ConfigManager.set(key, coerced)
        ConfigManager.save()

        # 同步写入实体目录的 config.json（如果存在）
        self._sync_entity_config_file(metadata.group, key, coerced)
        return True

    def update_entity_config_batch(
        self, name: str, updates: Dict[str, Any],
    ) -> int:
        """批量更新实体配置项（逐条类型矫正），返回成功更新数量。"""
        from core.config import ConfigManager

        metadata = self._resolve_metadata(name)
        if metadata is None:
            return 0

        count = 0
        coerced_updates: Dict[str, Any] = {}
        for key, value in updates.items():
            try:
                coerced = self._coerce_item_value(key, value)
            except ValueError:
                continue
            ConfigManager.set(key, coerced)
            coerced_updates[key] = coerced
            count += 1

        if count:
            ConfigManager.save()
            # 批量同步到 config.json
            self._sync_entity_config_file_batch(metadata.group, coerced_updates)

        return count

    def set_entity_enabled(self, name: str, enabled: bool) -> bool:
        """启用/禁用实体，并持久化到 app_config.json（实现归 EntityRegistry.set_enabled）。"""
        from core.entity import EntityRegistry

        return EntityRegistry.set_enabled(name, enabled)

    @staticmethod
    def apply_entity_states() -> int:
        """启动时恢复实体启用/禁用状态（实现归 agent.runtime.state_restore）。"""
        from agent.runtime.state_restore import apply_entity_states as _apply
        return _apply()

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
