"""人设管理服务 -- CRUD、激活切换。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class PersonaService:

    @staticmethod
    def _provider():
        from agent.ext.config_provider import get_config_provider
        return get_config_provider()

    def list_personas(self) -> List[Dict[str, Any]]:
        """返回人设列表。"""
        return self._provider().list_personas()

    def get_active(self) -> Optional[str]:
        """返回当前活跃人设标识。"""
        return self._provider().get_active_persona_name()

    def get_persona(self, key: str) -> Dict[str, Any]:
        """返回指定人设的配置数据。"""
        return self._provider().get_persona_config(key)

    def save_persona(self, key: str, data: Dict[str, Any]) -> None:
        """保存人设配置。"""
        self._provider().save_persona_config(key, data)

    def activate(self, key: str) -> bool:
        """设置活跃人设。"""
        return self._provider().set_active_persona(key)

    def create(self, key: str) -> None:
        """新建人设（默认空模板）。已存在时抛出异常。"""
        existing = [p["key"] for p in self.list_personas()]
        if key in existing:
            raise ValueError(f"人设 '{key}' 已存在")
        self._provider().save_persona_config(key, {
            "name": key,
            "description": "",
            "personality": [],
        })

    def delete(self, key: str) -> bool:
        """删除人设。不能删除活跃人设。"""
        return self._provider().delete_persona(key)
