"""配置服务 -- Mind 配置字段路由。"""

from __future__ import annotations

from typing import Any, Optional


class ConfigService:
    """配置服务（Web 侧入口）。"""

    _mind_fields_cache: Optional[frozenset] = None

    @classmethod
    def mind_fields(cls) -> frozenset:
        """MindConfig 字段集合（保存时路由到 save_mind_config 以保证双轨同步）。"""
        if cls._mind_fields_cache is None:
            try:
                from agent.config import MIND_SYNC_FIELDS
                cls._mind_fields_cache = frozenset((*MIND_SYNC_FIELDS, "tool_system_rules"))
            except Exception:
                cls._mind_fields_cache = frozenset()
        return cls._mind_fields_cache

    @staticmethod
    def save_mind_value(key: str, value: Any) -> None:
        """保存单个 MindConfig 字段（双轨同步 + 实时生效）。"""
        from agent.config import get_config_provider
        get_config_provider().save_mind_config(**{key: value})
