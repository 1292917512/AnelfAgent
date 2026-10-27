"""画像写入协调：主库提交为事实，ENTITY 镜像和在线缓存可重试同步。

模型看到：当前画像及写入结果中的 revision、同步状态；历史仅经查询按需读取。
Token：读取画像附带 64 字符 revision（数十 token），不自动注入历史。
缓存：当前画像仍走原有动态上下文，操作时间线不进入稳定前缀。
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from agent.storage.data_center import EverythingData
from agent.storage.profile_store import ProfileOperation, profile_revision
from core.config import get_config_int, register_configs_safe
from core.log import log

from .memory_store import MemoryStore
from .memory_types import MemoryEntry, MemoryType
from .self_profile import PROFILE_MEMORY_IMPORTANCE, SELF_PROFILE_SOURCE, SELF_SCOPE

register_configs_safe({"memory/profile_history": {
    "profile_history_days": {"description": "已完成画像操作的保留天数（不删除当前画像或未完成回执）", "default": 30, "min": 1, "unit": "天"},
    "profile_history_per_entity": {"description": "每个画像最多保留的已完成操作", "default": 50, "min": 1, "unit": "条"},
    "profile_history_max_rows": {"description": "画像历史已完成操作总量上限", "default": 5000, "min": 1, "unit": "条"},
}})


@dataclass(frozen=True)
class ProfileWriteResult:
    revision: str
    changed: bool
    synced: bool
    operation_id: str = ""


class ProfileWriter:
    """复用主库持有的协调锁，无独立后台任务或连接；恢复由既有心跳触发。"""

    def __init__(self, entities: EverythingData, store: MemoryStore | None) -> None:
        self.entities = entities
        self.sqlite = entities.router.sqlite
        self.store = store
        self.operations = self.sqlite.profiles

    async def read(self, scope_type: str, scope_id: str) -> dict[str, Any]:
        if scope_type not in {"user", "group", "agent"} or not scope_id or (scope_type == "agent" and scope_id != "self"):
            raise ValueError("无效的画像身份")
        primary = await self.sqlite.resolve_alias(scope_type, scope_id)
        p_type, p_id = primary or (scope_type, scope_id)
        data = await self.sqlite.get_entity_personality(scope_type=p_type, scope_id=p_id) or {}
        content = str(data.get("personality") or "")
        return {**data, "scope_type": p_type, "scope_id": p_id, "personality": content,
                "revision": profile_revision(p_type, p_id, content)}

    async def write(
        self, scope_type: str, scope_id: str, content: str, *, expected_revision: str,
        actor: str, reason: str = "", operation_id: str = "",
        source_memory_id: int | None = None, source_version: int | None = None, delete: bool = False,
    ) -> ProfileWriteResult:
        content = content.strip()
        if not content and not delete:
            raise ValueError("画像内容不能为空")
        async with self.operations.lock:
            current = await self.read(scope_type, scope_id)
            p_type, p_id = current["scope_type"], current["scope_id"]
            guard = (self.store.guard_version(source_memory_id, source_version)
                     if self.store is not None and source_memory_id is not None and source_version is not None
                     else nullcontext())
            async with guard:
                operation = await self.operations.commit(
                    p_type, p_id, content, expected_revision=expected_revision, actor=actor, reason=reason,
                    operation_id=operation_id, source_memory_id=source_memory_id, delete=delete,
                )
            if operation is None:
                recent = await self.operations.timeline(scope_type=p_type, scope_id=p_id, limit=1)
                pending = recent[0] if recent and not recent[0].synced else None
                synced = await self._sync(pending) if pending else True
                return ProfileWriteResult(current["revision"], False, synced, pending.operation_id if pending else "")
            synced = operation.synced or await self._sync(operation)
            latest = await self.read(p_type, p_id)
            return ProfileWriteResult(latest["revision"], current["revision"] != latest["revision"],
                                      synced, operation.operation_id)

    async def resume(self, operation_id: str) -> ProfileWriteResult | None:
        """提交后的重试只补齐同步，不覆盖后来产生的画像正文。"""
        async with self.operations.lock:
            operation = await self.operations.get_operation(operation_id)
            if operation is None:
                return None
            synced = operation.synced or await self._sync(operation)
            current = await self.read(operation.scope_type, operation.scope_id)
            return ProfileWriteResult(current["revision"], False, synced, operation_id)

    async def recover(self) -> int:
        """限量恢复未同步回执，不补写没有回执的历史画像。"""
        count = 0
        for operation in await self.operations.pending():
            result = await self.resume(operation.operation_id)
            count += bool(result and result.synced)
        if self.store is not None:
            for operation in await self.operations.unsettled():
                entry = await self.store.get(operation.source_memory_id) if operation.source_memory_id else None
                plan = (entry.metadata.get("lifecycle", {}).get("promotion") if entry else None)
                if not isinstance(plan, dict) or plan.get("operation_id") != operation.operation_id:
                    await self.operations.settle(operation.operation_id)
        await self.operations.prune(
            days=get_config_int("profile_history_days", 30),
            per_profile=get_config_int("profile_history_per_entity", 50),
            total=get_config_int("profile_history_max_rows", 5000),
        )
        return count

    async def _sync(self, operation: ProfileOperation) -> bool:
        try:
            # 读原始身份，不能把删除后的旧操作重新路由到新关联的主身份。
            row = await self.sqlite.get_entity_personality(
                scope_type=operation.scope_type, scope_id=operation.scope_id,
            )
            content = str((row or {}).get("personality") or "")
            await self._sync_mirror(operation.scope_type, operation.scope_id, content)
            keys = {(operation.scope_type, operation.scope_id)}
            aliases = await self.sqlite.get_aliases_for_primary(operation.scope_type, operation.scope_id)
            keys.update((a["scope_type"], a["scope_id"]) for a in aliases)
            for entity in list(self.entities.entities.values()):
                if entity.identity_parts in keys:
                    # 不调用 set_personality：同步不重置各身份独立的会话计数。
                    current = await self.read(*entity.identity_parts)
                    entity.personality["personality"] = current["personality"]
            await self.operations.mark_synced(operation)
            return True
        except Exception as exc:
            await self.operations.mark_synced(operation, error=str(exc))
            log(f"画像已提交，等待同步 {operation.operation_id}: {exc}", "WARNING", tag="记忆")
            return False

    async def _sync_mirror(self, scope_type: str, scope_id: str, content: str) -> None:
        if self.store is None:
            raise RuntimeError("画像记忆库未就绪")
        tag = f"{scope_type}:{scope_id}"
        source = SELF_PROFILE_SOURCE if tag == SELF_SCOPE else f"entity_{scope_id}"
        # 身份标签精确匹配，避免 user 与 group 共用旧 source 时相互覆盖。
        candidates = await self.store.search_by_tags([tag], limit=10000)
        mirrors = [e for e in candidates if e.memory_type == MemoryType.ENTITY and e.source == source]
        if not content:
            for entry in mirrors:
                if entry.id is not None:
                    await self.store.delete(entry.id, actor="profile_sync")
            return
        if mirrors:
            # 更新现有 ID；旧重复镜像也原位更新，不破坏 mem:ID 引用和既有元数据。
            for entry in mirrors:
                if entry.content == content:
                    continue
                entry.content = content
                if not await self.store.update(entry, expected_version=entry.version, actor="profile_sync"):
                    raise RuntimeError("画像镜像并发更新，等待重试")
        else:
            await self.store.add(MemoryEntry(
                memory_type=MemoryType.ENTITY, content=content, source=source,
                tags=[tag], importance=PROFILE_MEMORY_IMPORTANCE,
            ), actor="profile_sync")
            from .embedding import wake_embedding_worker
            wake_embedding_worker()
