"""配置服务 -- Mind 配置字段路由。"""

from __future__ import annotations

from typing import Any


class ConfigService:
    """配置服务（Web 侧入口）。"""

    @staticmethod
    def mind_fields() -> frozenset:
        """MindConfig 字段集合（保存时路由到 save_mind_config 以保证双轨同步）。"""
        try:
            from agent.config import MIND_CONFIG_FIELDS
            return frozenset(MIND_CONFIG_FIELDS)
        except Exception:
            return frozenset()

    @staticmethod
    def save_mind_value(key: str, value: Any) -> None:
        """保存单个 MindConfig 字段（双轨同步 + 实时生效）。"""
        from agent.config import get_config_provider
        get_config_provider().save_mind_config(**{key: value})
