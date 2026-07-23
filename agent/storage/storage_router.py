"""
StorageRouter：统一存储路由层。

根据数据域（Domain）将读写请求路由到合适的后端（SQLite）：

| Domain              | 后端       | 说明                                     |
|---------------------|-----------|------------------------------------------|
| conversation        | SQLite    | 会话记录（高频追加，定量裁剪）                 |
| entity_profile      | SQLite    | 实体画像/人格（低频 upsert，按 scope 检索）    |
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from agent.storage.sqlite_backend import SqliteBackend


class StorageDomain(str, Enum):
    """存储域枚举。"""

    CONVERSATION = "conversation"
    ENTITY_PROFILE = "entity_profile"


class StorageRouter:
    """统一存储路由：按域名将操作分派到 SQLite。"""

    def __init__(self, sqlite: Optional[SqliteBackend] = None, **_kw: Any) -> None:
        self.sqlite = sqlite or SqliteBackend()

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def append(self, domain: StorageDomain, **kwargs: Any) -> None:
        handler = self._dispatch_append(domain)
        await handler(**kwargs)

    async def fetch(self, domain: StorageDomain, **kwargs: Any) -> List[Dict[str, Any]]:
        handler = self._dispatch_fetch(domain)
        return await handler(**kwargs)

    async def upsert(self, domain: StorageDomain, **kwargs: Any) -> None:
        handler = self._dispatch_upsert(domain)
        await handler(**kwargs)

    async def get_one(self, domain: StorageDomain, **kwargs: Any) -> Optional[Any]:
        handler = self._dispatch_get_one(domain)
        return await handler(**kwargs)

    # ------------------------------------------------------------------
    # 路由分发
    # ------------------------------------------------------------------

    def _dispatch_append(self, domain: StorageDomain):
        return {
            StorageDomain.CONVERSATION: self._append_conversation,
            StorageDomain.ENTITY_PROFILE: self._upsert_entity_profile,
        }[domain]

    def _dispatch_fetch(self, domain: StorageDomain):
        return {
            StorageDomain.CONVERSATION: self._fetch_conversation,
            StorageDomain.ENTITY_PROFILE: self._fetch_entity_profile_list,
        }[domain]

    def _dispatch_upsert(self, domain: StorageDomain):
        return {
            StorageDomain.ENTITY_PROFILE: self._upsert_entity_profile,
        }.get(domain, self._noop_upsert)

    def _dispatch_get_one(self, domain: StorageDomain):
        return {
            StorageDomain.ENTITY_PROFILE: self._get_entity_personality,
        }.get(domain, self._noop_get_one)

    # ------------------------------------------------------------------
    # Conversation → SQLite
    # ------------------------------------------------------------------

    async def _append_conversation(self, *, scope_type: str, scope_id: str, role: str, content: str, ts_ns: Optional[int] = None, adapter_key: str = "", **_kw: Any) -> None:
        await self.sqlite.append_conversation(scope_type=scope_type, scope_id=scope_id, role=role, content=content, ts_ns=ts_ns, adapter_key=adapter_key)

    async def _fetch_conversation(self, *, scope_type: str, scope_id: str, limit: int = 30, **_kw: Any) -> List[Dict[str, Any]]:
        return await self.sqlite.fetch_conversation(scope_type=scope_type, scope_id=scope_id, limit=limit)

    # ------------------------------------------------------------------
    # Entity Profile → SQLite
    # ------------------------------------------------------------------

    async def _upsert_entity_profile(self, *, scope_type: str, scope_id: str, personality: str, **_kw: Any) -> None:
        conv_num = _kw.get("conv_num", 0)
        conv_update_num = _kw.get("conv_update_num", 0)
        await self.sqlite.set_entity_personality(
            scope_type=scope_type, scope_id=scope_id, personality=personality,
            conv_num=conv_num, conv_update_num=conv_update_num,
        )

    async def _get_entity_personality(self, *, scope_type: str, scope_id: str, **_kw: Any) -> Optional[dict]:
        """返回 {personality, conv_num, conv_update_num} 或 None。"""
        return await self.sqlite.get_entity_personality(scope_type=scope_type, scope_id=scope_id)

    async def _fetch_entity_profile_list(self, **_kw: Any) -> List[Dict[str, Any]]:
        """列出所有实体画像（委托 SQLite 真实查询）。"""
        return await self.sqlite.list_entity_profiles()

    # ------------------------------------------------------------------
    # Noop fallbacks
    # ------------------------------------------------------------------

    async def _noop_upsert(self, **_kw: Any) -> None:
        pass

    async def _noop_get_one(self, **_kw: Any) -> Optional[Any]:
        return None
