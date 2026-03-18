"""
StorageRouter：统一存储路由层。

根据数据域（Domain）将读写请求路由到合适的后端（SQLite / 内存）：

| Domain              | 后端       | 说明                                     |
|---------------------|-----------|------------------------------------------|
| conversation        | SQLite    | 会话记录（高频追加，定量裁剪）                 |
| entity_profile      | SQLite    | 实体画像/人格（低频 upsert，按 scope 检索）    |
| short_term_memory   | Memory    | 短期记忆/PFC 临时片段（纯内存，重启清空）       |
| task_queue          | Memory    | 运行时任务队列（不需持久化，进程级生命周期）      |
"""

from __future__ import annotations

import time
from collections import deque
from enum import Enum
from typing import Any, Dict, List, Optional

from agent.storage.sqlite_backend import SqliteBackend


class StorageDomain(str, Enum):
    """存储域枚举。"""

    CONVERSATION = "conversation"
    ENTITY_PROFILE = "entity_profile"
    SHORT_TERM_MEMORY = "short_term_memory"
    TASK_QUEUE = "task_queue"


class StorageRouter:
    """统一存储路由：按域名将操作分派到 SQLite / 内存。"""

    def __init__(self, sqlite: Optional[SqliteBackend] = None, **_kw: Any) -> None:
        self.sqlite = sqlite or SqliteBackend()
        self._task_queues: Dict[str, List[Dict[str, Any]]] = {}
        self._stm: Dict[str, deque[Dict[str, Any]]] = {}

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
            StorageDomain.SHORT_TERM_MEMORY: self._append_short_term_memory,
            StorageDomain.TASK_QUEUE: self._append_task_queue,
            StorageDomain.ENTITY_PROFILE: self._upsert_entity_profile,
        }[domain]

    def _dispatch_fetch(self, domain: StorageDomain):
        return {
            StorageDomain.CONVERSATION: self._fetch_conversation,
            StorageDomain.SHORT_TERM_MEMORY: self._fetch_short_term_memory,
            StorageDomain.TASK_QUEUE: self._fetch_task_queue,
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

    async def _append_conversation(self, *, scope_type: str, scope_id: str, role: str, content: str, **_kw: Any) -> None:
        await self.sqlite.append_conversation(scope_type=scope_type, scope_id=scope_id, role=role, content=content)

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
        return []

    # ------------------------------------------------------------------
    # Short-term Memory → Memory (deque, FIFO)
    # ------------------------------------------------------------------

    async def _append_short_term_memory(self, *, role: str, content: str, scope_key: str = "__global__", max_size: int = 20, **_kw: Any) -> None:
        if scope_key not in self._stm:
            self._stm[scope_key] = deque(maxlen=max_size)
        self._stm[scope_key].append({"role": role, "content": content, "ts": time.time()})

    async def _fetch_short_term_memory(self, *, scope_key: str = "__global__", limit: int = 20, **_kw: Any) -> List[Dict[str, Any]]:
        q = self._stm.get(scope_key)
        if not q:
            return []
        items = list(q)
        return items[-limit:]

    # ------------------------------------------------------------------
    # Task Queue → Memory
    # ------------------------------------------------------------------

    async def _append_task_queue(self, *, queue_name: str = "default", item: Dict[str, Any], **_kw: Any) -> None:
        self._task_queues.setdefault(queue_name, []).append(item)

    async def _fetch_task_queue(self, *, queue_name: str = "default", limit: int = 100, **_kw: Any) -> List[Dict[str, Any]]:
        items = self._task_queues.get(queue_name, [])
        return items[-limit:]

    # ------------------------------------------------------------------
    # Noop fallbacks
    # ------------------------------------------------------------------

    async def _noop_upsert(self, **_kw: Any) -> None:
        pass

    async def _noop_get_one(self, **_kw: Any) -> Optional[Any]:
        return None
