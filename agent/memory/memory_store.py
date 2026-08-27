"""MemoryStore：基于 SQLite FTS5 + Embedding 的统一记忆存储（门面）。

支持混合评分管线：语义评分 (Vector + FTS + TagMatch) × 衰减评分 (Recency + Frequency + Importance)。
新增文件索引体系：memory.md + memory/*.md 分块索引，双轨统一搜索。

本类为组合门面：连接/schema/vec 索引由 store.connection.MemoryConnectionManager 承担，
cognee 投影队列、文件/chunks 索引、工具错误追踪、搜索管线分别委托
store.cognee_queue / store.file_index / store.tool_errors / store.search 子模块；
公开方法签名与返回结构保持不变。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from contextlib import AbstractAsyncContextManager
from typing import Any, Dict, Optional

import aiosqlite

from core.entity import BaseEntity, EntityType
from core.log import log

from .memory_types import MemoryEntry, MemorySearchResult, MemoryType
from .memory_utils import cosine_similarity, pack_embedding
from .store._shared import (
    MEM_COLUMNS as _MEM_COLUMNS,
)
from .store._shared import (
    MEM_COLUMNS_NO_EMB as _MEM_COLUMNS_NO_EMB,
)
from .store._shared import (
    compute_effective_score as _compute_effective_score,
)
from .store._shared import (
    entry_projection_payload,
    row_to_entry,
)
from .store._shared import (
    get_memory_config_value as _get_memory_config_value,
)
from .store.cognee_queue import ENTRY_KIND_MEMORY, CogneeSyncQueue
from .store.connection import MemoryConnectionManager
from .store.file_index import FileIndexStore
from .store.search import SearchEngine
from .store.tag_intel import ENTITY_PREFIXES
from .store.tool_errors import ToolErrorTracker


def default_memory_db_path() -> str:
    """派生记忆库默认路径：主库 stem + "_memory"（历史布局）。"""
    from pathlib import Path

    from core.storage_volume import main_sqlite_path

    main = Path(main_sqlite_path())
    return str(main.with_name(f"{main.stem}_memory{main.suffix or '.sqlite3'}"))


def _register_volume() -> None:
    from core.storage_volume import VolumeDescriptor, VolumeKind, register_volume

    register_volume(VolumeDescriptor(
        volume_id="memory",
        name="长期记忆库",
        description="长期记忆 / 归档 / 文档索引 / 向量 / Cognee 同步队列",
        kind=VolumeKind.SQLITE,
        default_path=default_memory_db_path,
    ))


_register_volume()


class MemoryStore(BaseEntity):
    """SQLite 记忆存储，支持 FTS5 全文检索、向量相似度搜索和标签索引。"""

    _entity_type = EntityType.DATABASE
    _entity_description = "记忆存储 — 基于 SQLite FTS5 + Embedding 的统一记忆系统"
    _entity_meta = {
        "backend": "sqlite",
        "domains": ["memories", "file_index", "graph", "cognee_queue"],
    }

    def __init__(self, db_path: Optional[str] = None) -> None:
        from core.storage_volume import get_volume_registry

        from .graph import GraphStore

        self._db_path = db_path or get_volume_registry().resolve_path("memory")
        get_volume_registry().mark_active("memory", self._db_path)
        self._conn = MemoryConnectionManager(self._db_path)
        self._cognee = CogneeSyncQueue(self._conn)
        self._files = FileIndexStore(self._conn)
        self._tool_errors = ToolErrorTracker(self._conn)
        self._search = SearchEngine(self._conn, self._files)
        self.graph = GraphStore(self._conn, self._cognee)
        super().__init__()

    # ------------------------------------------------------------------
    # 连接管理（委托 MemoryConnectionManager，保持原有私有属性可访问）
    # ------------------------------------------------------------------

    @property
    def _db(self) -> Optional[aiosqlite.Connection]:
        return self._conn.db

    @property
    def _db_connect_lock(self) -> asyncio.Lock:
        return self._conn.connect_lock

    @property
    def _initialized(self) -> bool:
        return self._conn.initialized

    @property
    def _fts_available(self) -> bool:
        return self._conn.fts_available

    @property
    def _chunks_fts_available(self) -> bool:
        return self._conn.chunks_fts_available

    @property
    def _vec_available(self) -> bool:
        return self._conn.vec_available

    @_vec_available.setter
    def _vec_available(self, value: bool) -> None:
        self._conn.vec_available = value

    @property
    def _vec_dims(self) -> Optional[int]:
        return self._conn.vec_dims

    @_vec_dims.setter
    def _vec_dims(self, value: Optional[int]) -> None:
        self._conn.vec_dims = value

    @property
    def _cognee_projection_enabled(self) -> bool:
        return self._cognee.enabled

    def set_cognee_projection_enabled(self, enabled: bool) -> None:
        """启用或禁用 Cognee 持久化投影队列。"""
        self._cognee.set_enabled(enabled)

    def _tx(self, db: aiosqlite.Connection) -> AbstractAsyncContextManager[None]:
        """事务模板：复合写全部成功才 commit，任一异常 rollback（见 connection.tx）。"""
        return self._conn.tx(db)

    async def _get_db(self) -> aiosqlite.Connection:
        return await self._conn.get_db()

    async def close(self) -> None:
        # 与 _get_db 的健康检查/重建共用同一把锁，避免关闭途中被其他协程复用
        await self._conn.close()

    # ------------------------------------------------------------------
    # Embedding 回填
    # ------------------------------------------------------------------

    async def backfill_embeddings(self, embedder: Any, batch_size: int = 32) -> int:
        """批量为缺少 embedding 的记忆补充向量（单次 API 调用处理一批）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, content FROM memories WHERE embedding_blob IS NULL LIMIT ?",
            (batch_size,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        vecs = await embedder.embed_text([row["content"] for row in rows])
        if len(vecs) != len(rows):
            return 0

        count = 0
        for row, vec in zip(rows, vecs, strict=False):
            if not vec:
                continue
            blob = pack_embedding(vec)
            await db.execute(
                "UPDATE memories SET embedding_blob=? WHERE id=?",
                (blob, row["id"]),
            )
            await self._conn.vec_upsert_memory(db, int(row["id"]), blob)
            count += 1
        if count:
            await db.commit()
            log(f"Embedding 批量回填: {count} 条记忆", "DEBUG", tag="思维")
        return count

    async def backfill_chunk_embeddings(self, embedder: Any, batch_size: int = 32) -> int:
        """批量为缺少 embedding 的文件 chunk 补充向量，并写入 embedding 缓存。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, hash, text FROM chunks WHERE embedding IS NULL LIMIT ?",
            (batch_size,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        vecs = await embedder.embed_text([row["text"] for row in rows])
        if len(vecs) != len(rows):
            return 0

        now_ns = int(time.time() * 1e9)
        count = 0
        for row, vec in zip(rows, vecs, strict=False):
            if not vec:
                continue
            blob = pack_embedding(vec)
            await db.execute(
                "UPDATE chunks SET embedding=? WHERE id=?",
                (blob, row["id"]),
            )
            await self._conn.vec_upsert_chunk(db, str(row["id"]), blob)
            await db.execute(
                "INSERT OR REPLACE INTO embedding_cache(hash, embedding, dims, updated_ns) "
                "VALUES(?,?,?,?)",
                (row["hash"], blob, len(vec), now_ns),
            )
            count += 1
        if count:
            await db.commit()
            log(f"Embedding 批量回填: {count} 条 chunk", "DEBUG", tag="思维")
        return count

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def add(self, entry: MemoryEntry) -> int:
        """添加一条记忆，返回 id。

        migrated=1：新记忆诞生于现行体系，无需参与旧版 MD 转储迁移
        （migrated=0 仅标识需要导出到便签的历史遗留行）。
        """
        db = await self._get_db()
        ts_ns = int(entry.timestamp * 1e9) if entry.timestamp else int(time.time() * 1e9)
        blob = pack_embedding(entry.embedding) if entry.embedding else None
        tags_json = json.dumps(entry.tags, ensure_ascii=False)
        async with self._tx(db):
            cursor = await db.execute(
                "INSERT INTO memories"
                "(type, content, source, importance, ts_ns, metadata_json, embedding_blob, tags_json, access_count, last_accessed_ns, migrated) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,1)",
                (
                    entry.memory_type.value,
                    entry.content,
                    entry.source,
                    entry.importance,
                    ts_ns,
                    json.dumps(entry.metadata, ensure_ascii=False),
                    blob,
                    tags_json,
                    entry.access_count,
                    int(entry.last_accessed * 1e9),
                ),
            )
            row_id = cursor.lastrowid or 0
            await self._conn.vec_upsert_memory(db, row_id, blob)
            await self._conn.fts_upsert_memory(db, row_id, entry.content)
            await self._cognee.enqueue_sync(
                db,
                row_id,
                "upsert",
                entry_projection_payload(entry, row_id),
            )
        tag_hint = f" tags={entry.tags}" if entry.tags else ""
        log(f"📝 记忆写入 [{entry.memory_type.value}] id={row_id}{tag_hint}: {entry.content[:50]}", tag="思维")
        return row_id

    async def get(self, memory_id: int) -> Optional[MemoryEntry]:
        db = await self._get_db()
        cursor = await db.execute(f"SELECT {_MEM_COLUMNS} FROM memories WHERE id=?", (memory_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return row_to_entry(row)

    async def update_importance(self, memory_id: int, importance: float) -> None:
        db = await self._get_db()
        async with self._tx(db):
            await db.execute("UPDATE memories SET importance=? WHERE id=?", (importance, memory_id))
            entry = await self.get(memory_id)
            if entry:
                await self._cognee.enqueue_sync(
                    db, memory_id, "upsert", entry_projection_payload(entry, memory_id),
                )

    async def update(self, entry: MemoryEntry, *, clear_embedding: bool = False) -> bool:
        """原地更新一条记忆的内容、标签等字段（保留原 id 和时间戳）。

        embedding 语义：entry.embedding 非空时写入新向量；clear_embedding=True 时
        显式清空（内容已变更，待后台 worker 重建）；其余情况不触碰 embedding_blob。
        """
        if not entry.id:
            return False
        db = await self._get_db()
        set_clause = "content=?, importance=?, metadata_json=?, tags_json=?, version=version+1"
        params: list[Any] = [
            entry.content,
            entry.importance,
            json.dumps(entry.metadata, ensure_ascii=False),
            json.dumps(entry.tags, ensure_ascii=False),
        ]
        blob: Optional[bytes] = None
        embedding_touched = False
        if entry.embedding:
            blob = pack_embedding(entry.embedding)
            set_clause += ", embedding_blob=?"
            params.append(blob)
            embedding_touched = True
        elif clear_embedding:
            set_clause += ", embedding_blob=NULL"
            embedding_touched = True
        params.append(entry.id)
        async with self._tx(db):
            cursor = await db.execute(
                f"UPDATE memories SET {set_clause} WHERE id=?",
                params,
            )
            if (cursor.rowcount or 0) > 0:
                if embedding_touched:
                    await self._conn.vec_upsert_memory(db, entry.id or 0, blob)
                await self._conn.fts_upsert_memory(db, entry.id or 0, entry.content)
                await self._record_audit(db, entry.id or 0, "update", entry.content[:80])
                await self._cognee.enqueue_sync(
                    db,
                    entry.id,
                    "upsert",
                    entry_projection_payload(entry, entry.id),
                )
        updated = (cursor.rowcount or 0) > 0
        if updated:
            log(f"📝 记忆更新 [{entry.memory_type.value}] id={entry.id}: {entry.content[:50]}", tag="思维")
        return updated

    async def delete(self, memory_id: int) -> bool:
        db = await self._get_db()
        async with self._tx(db):
            cursor = await db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
            if (cursor.rowcount or 0) > 0:
                await self._conn.vec_delete_memories(db, [memory_id])
                await self._conn.fts_delete_memories(db, [memory_id])
                await self._record_audit(db, memory_id, "delete")
                await self._cognee.enqueue_sync(db, memory_id, "delete")
        return (cursor.rowcount or 0) > 0

    async def archive_memory(self, memory_id: int, reason: str = "manual_forget") -> bool:
        """软删除：将记忆移入归档表（不参与召回，可 restore_memory 恢复）。"""
        entry = await self.get(memory_id)
        if entry is None:
            return False
        db = await self._get_db()
        async with self._tx(db):
            await self._archive_entry(entry, reason)
        return True

    async def clear(
        self,
        memory_type: Optional[MemoryType] = None,
        include_permanent: bool = False,
    ) -> int:
        """清除记忆。默认跳过 permanent 类型。"""
        db = await self._get_db()
        select_sql = "SELECT id FROM memories"
        select_params: tuple[Any, ...] = ()
        if memory_type:
            select_sql += " WHERE type=?"
            select_params = (memory_type.value,)
            delete_sql = "DELETE FROM memories WHERE type=?"
            delete_params = select_params
        elif include_permanent:
            delete_sql = "DELETE FROM memories"
            delete_params = ()
        else:
            select_sql += " WHERE type != ?"
            select_params = (MemoryType.PERMANENT.value,)
            delete_sql = "DELETE FROM memories WHERE type != ?"
            delete_params = select_params
        ids_cursor = await db.execute(select_sql, select_params)
        memory_ids = [int(row["id"]) for row in await ids_cursor.fetchall()]
        async with self._tx(db):
            cursor = await db.execute(delete_sql, delete_params)
            await self._conn.vec_delete_memories(db, memory_ids)
            await self._conn.fts_delete_memories(db, memory_ids)
            for memory_id in memory_ids:
                await self._cognee.enqueue_sync(db, memory_id, "delete")
        return cursor.rowcount or 0

    # ------------------------------------------------------------------
    # 审计（事件流只追加：谁改了哪条、何时、因何）
    # ------------------------------------------------------------------

    @staticmethod
    async def _record_audit(
        db: aiosqlite.Connection, memory_id: int, action: str, detail: str = "",
    ) -> None:
        """追加一条记忆审计事件（失败不影响主流程）。"""
        try:
            await db.execute(
                "INSERT INTO memory_audit(memory_id, action, detail, ts_ns) VALUES(?,?,?,?)",
                (memory_id, action, detail[:200], int(time.time() * 1e9)),
            )
        except Exception as exc:
            log(f"记忆审计写入失败: {exc}", "DEBUG")

    async def get_audit_summary(self, hours: float = 24.0) -> Dict[str, int]:
        """近 N 小时的审计事件统计（按动作分类计数）。"""
        db = await self._get_db()
        cutoff = int((time.time() - hours * 3600) * 1e9)
        cursor = await db.execute(
            "SELECT action, COUNT(*) AS c FROM memory_audit WHERE ts_ns > ? GROUP BY action",
            (cutoff,),
        )
        return {str(r["action"]): int(r["c"]) for r in await cursor.fetchall()}

    async def list_audit(self, memory_id: int = 0, limit: int = 50) -> list[Dict[str, Any]]:
        """查询审计事件（memory_id=0 时返回全局最近事件）。"""
        db = await self._get_db()
        if memory_id:
            cursor = await db.execute(
                "SELECT memory_id, action, detail, ts_ns FROM memory_audit "
                "WHERE memory_id=? ORDER BY id DESC LIMIT ?",
                (memory_id, limit),
            )
        else:
            cursor = await db.execute(
                "SELECT memory_id, action, detail, ts_ns FROM memory_audit "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        return [
            {"memory_id": r["memory_id"], "action": r["action"],
             "detail": r["detail"], "timestamp": r["ts_ns"] / 1e9}
            for r in await cursor.fetchall()
        ]

    # ------------------------------------------------------------------
    # Cognee 投影队列（委托 CogneeSyncQueue）
    # ------------------------------------------------------------------

    async def claim_cognee_sync_batch(self, limit: int) -> list[Dict[str, Any]]:
        """领取一批可执行投影任务，避免同进程重复消费。"""
        return await self._cognee.claim_batch(limit)

    async def requeue_stale_cognee_sync(self, stale_after_seconds: float = 900.0) -> int:
        """回收卡死的投影任务：processing 超过阈值时间的重置为 pending。"""
        return await self._cognee.requeue_stale(stale_after_seconds)

    async def complete_cognee_sync(
        self,
        queue_id: int,
        entry_id: int,
        *,
        entry_kind: str = ENTRY_KIND_MEMORY,
        dataset_name: str = "",
        dataset_id: str = "",
        data_id: str = "",
        content_hash: str = "",
        delete_mapping: bool = False,
    ) -> None:
        await self._cognee.complete(
            queue_id, entry_id,
            entry_kind=entry_kind,
            dataset_name=dataset_name, dataset_id=dataset_id,
            data_id=data_id, content_hash=content_hash,
            delete_mapping=delete_mapping,
        )

    async def fail_cognee_sync(
        self,
        queue_id: int,
        error: str,
        *,
        max_retries: int,
        retry_delay_seconds: float,
    ) -> None:
        await self._cognee.fail(
            queue_id, error,
            max_retries=max_retries, retry_delay_seconds=retry_delay_seconds,
        )

    async def get_cognee_mapping(
        self,
        entry_id: int,
        *,
        entry_kind: str = ENTRY_KIND_MEMORY,
    ) -> Optional[Dict[str, Any]]:
        return await self._cognee.get_mapping(entry_id, entry_kind=entry_kind)

    async def get_cognee_sync_status(self) -> Dict[str, int]:
        return await self._cognee.get_status()

    async def reset_cognee_projection(self) -> Dict[str, int]:
        """清空 cognee 投影队列与 ID 映射（清空重建 cognee 数据前调用）。"""
        return await self._cognee.reset()

    async def retry_failed_cognee_sync(self) -> int:
        return await self._cognee.retry_failed()

    async def enqueue_cognee_backfill(self, *, limit: int = 0) -> int:
        """显式将历史记忆加入投影队列；不会在启动时自动调用。"""
        return await self._cognee.enqueue_backfill(limit=limit)

    # ------------------------------------------------------------------
    # 访问跟踪（隐式反馈回路）
    # ------------------------------------------------------------------

    async def record_access(self, memory_ids: list[int]) -> None:
        """批量记录记忆被访问（递增 access_count，更新 last_accessed）。

        强化机制：被召回的记忆 importance 微升（每次 +0.02，封顶 1.0），
        越常被想起的记忆越难忘（permanent 无需强化）。
        """
        if not memory_ids:
            return
        db = await self._get_db()
        now_ns = int(time.time() * 1e9)
        # 访问计数对所有类型记录；重要性强化仅非永久记忆（单条批量 UPDATE）
        placeholders = ",".join("?" for _ in memory_ids)
        async with self._tx(db):
            await db.execute(
                f"UPDATE memories SET access_count = access_count + 1, last_accessed_ns = ?, "
                f"importance = CASE WHEN type != 'permanent' "
                f"THEN MIN(1.0, importance + 0.02) ELSE importance END "
                f"WHERE id IN ({placeholders})",
                (now_ns, *memory_ids),
            )

    async def relax_importance(self, stale_days: int = 14, rate: float = 0.05) -> int:
        """重要性松弛：长期未被访问的非永久记忆，importance 向基线 0.5 缓慢回归。

        对称化 record_access 的 +0.02 强化——强化只升不降会导致 importance
        长期趋同于 1.0 失去区分度。每次整理对超过 stale_days 未访问且高于
        基线的记忆按比例下调；低于基线的交给有效分时间衰减与遗忘流程。
        permanent 与已合并（importance=0）记忆豁免。
        批量调整后对受影响条目补 cognee 投影（上限 200 条/轮，防投影风暴），
        保证权威层与投影层不长期漂移。
        Returns: 调整条数。
        """
        if rate <= 0:
            return 0
        db = await self._get_db()
        cutoff_ns = int((time.time() - stale_days * 86400) * 1e9)
        where = ("importance > 0.5 AND type != 'permanent' "
                 "AND COALESCE(last_accessed_ns, ts_ns) < ?")
        # 先取受影响行（投影同步需要条快照），再批量更新；
        # 无向量列：投影负载不含 embedding，跳过多余解包
        cursor = await db.execute(
            f"SELECT {_MEM_COLUMNS_NO_EMB} FROM memories WHERE {where} LIMIT 500",
            (cutoff_ns,),
        )
        affected = [row_to_entry(r, with_embedding=False) for r in await cursor.fetchall()]
        async with self._tx(db):
            cursor = await db.execute(
                "UPDATE memories SET importance = 0.5 + (importance - 0.5) * (1.0 - ?) "
                f"WHERE {where}",
                (min(rate, 1.0), cutoff_ns),
            )
            # 投影同步：用更新后的 importance 重建负载（封顶防风暴）
            for entry in affected[:200]:
                entry.importance = 0.5 + (entry.importance - 0.5) * (1.0 - min(rate, 1.0))
                await self._cognee.enqueue_sync(
                    db, entry.id, "upsert",
                    entry_projection_payload(entry, entry.id),
                )
        return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

    # ------------------------------------------------------------------
    # 遗忘机制（有效分评估 + 自动清理）
    # ------------------------------------------------------------------

    @staticmethod
    def compute_effective_score(entry: MemoryEntry, now: Optional[float] = None) -> float:
        """计算记忆的有效分：importance × 时间衰减 × 访问强化。

        有效分模拟人脑遗忘曲线：重要性是基础，时间推移衰减，
        频繁访问的记忆获得强化抵抗遗忘。permanent 永远返回 1.0（不遗忘）。
        """
        return _compute_effective_score(entry, now)

    async def _archive_entry(self, entry: MemoryEntry, reason: str) -> None:
        """将记忆移入归档表（软遗忘：不参与召回，可恢复，向量随档保留）。"""
        if entry.id is None:
            return
        db = await self._get_db()
        await db.execute(
            "INSERT OR REPLACE INTO memories_archive "
            "(id, type, content, source, importance, ts_ns, metadata_json, "
            "tags_json, access_count, archived_at_ns, archive_reason, "
            "embedding_blob, last_accessed_ns, version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.id, entry.memory_type.value, entry.content, entry.source,
                entry.importance, int(entry.timestamp * 1e9),
                json.dumps(entry.metadata, ensure_ascii=False),
                json.dumps(entry.tags, ensure_ascii=False),
                entry.access_count, int(time.time() * 1e9), reason,
                pack_embedding(entry.embedding) if entry.embedding else None,
                int(entry.last_accessed * 1e9),
                entry.version,
            ),
        )
        await db.execute("DELETE FROM memories WHERE id = ?", (entry.id,))
        await self._conn.vec_delete_memories(db, [entry.id])
        await self._conn.fts_delete_memories(db, [entry.id])
        await self._record_audit(db, entry.id, "archive", reason)
        await self._cognee.enqueue_sync(db, entry.id, "delete")

    async def restore_memory(self, memory_id: int) -> bool:
        """从归档恢复记忆（回到活跃记忆库，向量与最近访问时间原样回填）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT * FROM memories_archive WHERE id = ?", (memory_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        blob: Optional[bytes] = row["embedding_blob"] if "embedding_blob" in row.keys() else None
        last_accessed_ns = row["last_accessed_ns"] if "last_accessed_ns" in row.keys() else 0
        version = int(row["version"]) if "version" in row.keys() and row["version"] else 1
        async with self._tx(db):
            await db.execute(
                "INSERT OR REPLACE INTO memories "
                "(id, type, content, source, importance, ts_ns, metadata_json, "
                "embedding_blob, tags_json, access_count, last_accessed_ns, migrated, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    row["id"], row["type"], row["content"], row["source"],
                    row["importance"], row["ts_ns"], row["metadata_json"],
                    blob,
                    row["tags_json"], row["access_count"], last_accessed_ns,
                    version,
                ),
            )
            await self._conn.vec_upsert_memory(db, memory_id, blob)
            await self._conn.fts_upsert_memory(db, memory_id, row["content"])
            await db.execute("DELETE FROM memories_archive WHERE id = ?", (memory_id,))
            # 归档时入队了 delete，恢复必须补 upsert，否则 cognee 侧残留已删除状态
            entry = await self.get(memory_id)
            if entry:
                await self._cognee.enqueue_sync(
                    db, memory_id, "upsert",
                    entry_projection_payload(entry, memory_id),
                )
        return True

    async def count_archived(self) -> int:
        """归档表中的记忆条数（状态展示用）。"""
        db = await self._get_db()
        cursor = await db.execute("SELECT COUNT(*) AS cnt FROM memories_archive")
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def list_archived(self, limit: int = 50) -> list[Dict[str, Any]]:
        """列出已归档的记忆（遗忘记录）。"""
        db = await self._get_db()
        cursor = await db.execute(
            "SELECT id, type, content, importance, archived_at_ns, archive_reason "
            "FROM memories_archive ORDER BY archived_at_ns DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"], "type": r["type"], "content": r["content"][:100],
                "importance": r["importance"], "archived_at": r["archived_at_ns"] / 1e9,
                "reason": r["archive_reason"],
            }
            for r in rows
        ]

    async def purge_archived_memories(self, older_than_days: int, limit: int = 500) -> int:
        """物理删除超过保留期的归档记忆，防止归档表无限增长。

        Args:
            older_than_days: 归档保留天数（按 archived_at_ns 计算），<=0 时不清理。
            limit: 单次最多删除条数。
        Returns:
            实际删除的条数。
        """
        if older_than_days <= 0:
            return 0
        db = await self._get_db()
        cutoff_ns = int((time.time() - older_than_days * 86400) * 1e9)
        async with self._tx(db):
            cursor = await db.execute(
                "DELETE FROM memories_archive WHERE id IN "
                "(SELECT id FROM memories_archive WHERE archived_at_ns < ? LIMIT ?)",
                (cutoff_ns, limit),
            )
        deleted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        if deleted:
            log(f"归档清理: 物理删除 {deleted} 条超过 {older_than_days} 天的归档记忆", tag="思维")
        return deleted

    async def forget_weak_memories(
            self,
            *,
            min_age_days: int = 30,
            score_threshold: float = 0.08,
            limit: int = 100,
            min_keep_per_type: Optional[int] = None,
    ) -> Dict[str, Any]:
        """遗忘低价值记忆：有效分低于阈值且超过最小年龄的非永久记忆。

        保守策略：permanent 豁免 + 最小年龄保护（新记忆不遗忘）+
        每类最小保留护栏（min_keep_per_type，None 时读配置，默认 20）。
        遗忘为归档制（memories_archive）：不参与召回，但可通过 restore_memory 恢复。
        Returns:
            遗忘报告 {forgotten: [{id, type, score}], count}
        """
        db = await self._get_db()
        now = time.time()
        from core.config import get_config_int
        min_ts = now - min_age_days * 86400
        # 无向量列 + 候选上限：有效分计算不需要 embedding，
        # 候选放大到 limit 的 3 倍足够覆盖护栏跳过的条目
        candidate_limit = max(limit * 3, limit + 50)
        cursor = await db.execute(
            f"SELECT {_MEM_COLUMNS_NO_EMB} FROM memories "
            "WHERE type != 'permanent' AND ts_ns < ? "
            "ORDER BY importance ASC, last_accessed_ns ASC LIMIT ?",
            (int(min_ts * 1e9), candidate_limit),
        )
        rows = await cursor.fetchall()

        forgotten: list[Dict[str, Any]] = []
        to_archive: list[tuple[MemoryEntry, float]] = []
        # 最小保留护栏：每类活跃记忆低于下限后不再遗忘（防极端清理掏空小类）
        min_keep = (
            min_keep_per_type if min_keep_per_type is not None
            # 该项注册在 ConfigManager（非 MindConfig 字段），须读配置中心热值
            else int(get_config_int("memory_forget_min_keep_per_type", 20))
        )
        type_counts = await self.get_type_counts()
        for row in rows:
            entry = row_to_entry(row, with_embedding=False)
            score = self.compute_effective_score(entry, now)
            if score < score_threshold and entry.id is not None:
                remaining = type_counts.get(entry.memory_type.value, 0)
                if remaining <= min_keep:
                    continue
                type_counts[entry.memory_type.value] = remaining - 1
                forgotten.append({
                    "id": entry.id,
                    "type": entry.memory_type.value,
                    "score": round(score, 4),
                    "preview": entry.content[:50],
                })
                to_archive.append((entry, score))
                if len(forgotten) >= limit:
                    break

        # 归档保留向量（恢复时无需重嵌）：仅对待归档子集批量取向量
        if to_archive:
            from .memory_utils import unpack_embedding
            archive_ids = [e.id for e, _ in to_archive if e.id is not None]
            placeholders = ",".join("?" for _ in archive_ids)
            emb_cursor = await db.execute(
                f"SELECT id, embedding_blob FROM memories WHERE id IN ({placeholders})",
                archive_ids,
            )
            emb_map = {int(r["id"]): r["embedding_blob"] for r in await emb_cursor.fetchall()}
            for entry, _ in to_archive:
                blob = emb_map.get(entry.id or 0)
                if blob:
                    entry.embedding = unpack_embedding(blob)

        # 复用第一轮已取出的行，不再逐条 get()
        async with self._tx(db):
            for entry, score in to_archive:
                await self._archive_entry(entry, f"低有效分遗忘 (score={round(score, 4)})")
        return {"forgotten": forgotten, "count": len(forgotten)}

    async def enforce_type_limits(
            self,
            max_per_type: Optional[int] = None,
    ) -> Dict[str, int]:
        """强制每类记忆数量上限：超出时删除该类有效分最低的条目。

        permanent 类不受限。Returns: {type: 删除数量}
        """
        if max_per_type is None:
            max_per_type = int(_get_memory_config_value("memory_max_per_type", 500))
        db = await self._get_db()
        now = time.time()
        removed: Dict[str, int] = {}

        cursor = await db.execute(
            "SELECT type, COUNT(*) as cnt FROM memories GROUP BY type"
        )
        for row in await cursor.fetchall():
            mem_type, count = row["type"], row["cnt"]
            if mem_type == MemoryType.PERMANENT.value or count <= max_per_type:
                continue
            excess = count - max_per_type
            # 只取最可能被淘汰的候选集（低重要性 + 最久未访问），
            # 避免把超限类型的整类记忆读入内存逐条算分
            candidate_limit = max(excess * 3, excess + 20)
            cursor2 = await db.execute(
                f"SELECT {_MEM_COLUMNS} FROM memories WHERE type = ? "
                "ORDER BY importance ASC, last_accessed_ns ASC LIMIT ?",
                (mem_type, candidate_limit),
            )
            entries = [row_to_entry(r) for r in await cursor2.fetchall()]
            entries.sort(key=lambda e: self.compute_effective_score(e, now))
            async with self._tx(db):
                for entry in entries[:excess]:
                    if entry.id is not None:
                        await self._archive_entry(entry, "类型上限清理")
            removed[mem_type] = excess

        return removed

    async def find_similar_memories(
            self,
            similarity_threshold: float = 0.92,
            *,
            limit: int = 50,
    ) -> list[tuple[MemoryEntry, MemoryEntry, float]]:
        """查找高相似度记忆对（向量余弦相似度），供自动合并。

        Returns: [(entry_a, entry_b, similarity)] 按相似度降序。
        """
        db = await self._get_db()
        if self._conn.vec_available and self._conn.vec_dims:
            # ANN 邻居法：每条向量查 k 近邻（O(n·k)），替代全配对 O(n²) 纯 Python 余弦
            return await self._find_similar_pairs_ann(db, similarity_threshold, limit)
        cursor = await db.execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE embedding_blob IS NOT NULL "
            "AND type != 'permanent' ORDER BY ts_ns DESC LIMIT 500"
        )
        entries = [row_to_entry(r) for r in await cursor.fetchall()]
        vecs: Dict[int, list[float]] = {}
        for e in entries:
            if e.id is not None and e.embedding:
                vecs[e.id] = e.embedding

        entry_list = [e for e in entries if e.id in vecs]
        # O(n²) 相似度计算放工作线程，避免阻塞事件循环
        return await asyncio.to_thread(
            self._find_similar_pairs, entry_list, vecs, similarity_threshold, limit,
        )

    async def _find_similar_pairs_ann(
            self,
            db: aiosqlite.Connection,
            similarity_threshold: float,
            limit: int,
    ) -> list[tuple[MemoryEntry, MemoryEntry, float]]:
        """ANN 邻居法相似对查找：每条向量查 k 近邻，阈值过滤后按相似度降序。"""
        cursor = await db.execute(
            "SELECT id, type, embedding_blob FROM memories WHERE embedding_blob IS NOT NULL "
            "AND type != 'permanent' ORDER BY ts_ns DESC LIMIT 500"
        )
        rows = await cursor.fetchall()
        if not rows:
            return []
        type_map = {int(r["id"]): r["type"] for r in rows}
        # 候选对聚合：同对去重保留最大相似度（k=自身+5 邻居）
        pair_sims: Dict[tuple[int, int], float] = {}
        for r in rows:
            rid = int(r["id"])
            cur = await db.execute(
                "SELECT rowid, distance FROM memories_vec "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (r["embedding_blob"], 6),
            )
            for nr in await cur.fetchall():
                nid = int(nr["rowid"])
                if nid == rid or nid not in type_map:
                    continue
                if type_map[nid] != type_map[rid]:
                    continue
                sim = 1.0 - float(nr["distance"])
                if sim < similarity_threshold:
                    continue
                key = (min(rid, nid), max(rid, nid))
                if sim > pair_sims.get(key, 0.0):
                    pair_sims[key] = sim
        if not pair_sims:
            return []
        ranked = sorted(pair_sims.items(), key=lambda kv: -kv[1])[:limit]
        involved = sorted({i for (a, b), _ in ranked for i in (a, b)})
        placeholders = ",".join("?" for _ in involved)
        cur = await db.execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE id IN ({placeholders})",
            involved,
        )
        entry_map = {int(r["id"]): row_to_entry(r) for r in await cur.fetchall()}
        return [
            (entry_map[a], entry_map[b], sim)
            for (a, b), sim in ranked
            if a in entry_map and b in entry_map
        ]

    @staticmethod
    def _find_similar_pairs(
            entry_list: list[MemoryEntry],
            vecs: Dict[int, list[float]],
            similarity_threshold: float,
            limit: int,
    ) -> list[tuple[MemoryEntry, MemoryEntry, float]]:
        """在候选向量中枚举高相似度记忆对（纯计算，供 asyncio.to_thread 调用）。"""
        pairs: list[tuple[MemoryEntry, MemoryEntry, float]] = []
        for i in range(len(entry_list)):
            for j in range(i + 1, len(entry_list)):
                a, b = entry_list[i], entry_list[j]
                if a.memory_type != b.memory_type:
                    continue
                sim = cosine_similarity(vecs[a.id], vecs[b.id])  # type: ignore[index]
                if sim >= similarity_threshold:
                    pairs.append((a, b, sim))
            if len(pairs) >= limit:
                break
        pairs.sort(key=lambda p: p[2], reverse=True)
        return pairs[:limit]

    async def merge_pair(
            self,
            keep_id: int,
            drop_id: int,
    ) -> bool:
        """合并记忆对：保留 keep，drop 的 tags/访问次数并入后删除。"""
        db = await self._get_db()
        keep = await self.get(keep_id)
        drop = await self.get(drop_id)
        if keep is None or drop is None:
            return False
        merged_tags = list(dict.fromkeys(keep.tags + drop.tags))
        async with self._tx(db):
            await db.execute(
                "UPDATE memories SET tags_json = ?, access_count = access_count + ?, "
                "importance = MAX(importance, ?) WHERE id = ?",
                (json.dumps(merged_tags, ensure_ascii=False), drop.access_count,
                 drop.importance, keep_id),
            )
            await db.execute("DELETE FROM memories WHERE id = ?", (drop_id,))
            await self._conn.vec_delete_memories(db, [drop_id])
            await self._conn.fts_delete_memories(db, [drop_id])
            await self._record_audit(db, drop_id, "merge", f"并入 #{keep_id}")
            await self._cognee.enqueue_sync(db, drop_id, "delete")
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def list_recent(
        self,
        limit: int = 20,
        memory_type: Optional[MemoryType] = None,
        source: Optional[str] = None,
    ) -> list[MemoryEntry]:
        db = await self._get_db()
        sql = f"SELECT {_MEM_COLUMNS} FROM memories WHERE 1=1"
        params: list[Any] = []
        if memory_type:
            sql += " AND type=?"
            params.append(memory_type.value)
        if source is not None:
            sql += " AND source=?"
            params.append(source)
        sql += " ORDER BY ts_ns DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [row_to_entry(r) for r in reversed(rows)]

    async def list_by_source(
        self,
        source: str,
        *,
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryEntry]:
        """按 source 分页查询记忆（按 ts_ns 降序，返回序稳定，适合翻页遍历）。

        与 list_recent 不同：不做 reverse，保证 OFFSET 翻页时各页衔接无重叠。
        """
        db = await self._get_db()
        sql = f"SELECT {_MEM_COLUMNS} FROM memories WHERE source=?"
        params: list[Any] = [source]
        if memory_type:
            sql += " AND type=?"
            params.append(memory_type.value)
        sql += " ORDER BY ts_ns DESC LIMIT ? OFFSET ?"
        params.append(limit)
        params.append(max(0, offset))
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [row_to_entry(r) for r in rows]

    async def count(self, memory_type: Optional[MemoryType] = None) -> int:
        db = await self._get_db()
        if memory_type:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM memories WHERE type=?", (memory_type.value,))
        else:
            cursor = await db.execute("SELECT COUNT(*) as cnt FROM memories")
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # 搜索管线（委托 SearchEngine）
    # ------------------------------------------------------------------

    async def list_tags(self) -> Dict[str, int]:
        """聚合所有标签及其出现次数。"""
        return await self._search.list_tags()

    async def cooccurring_tags(
        self, seed_tags: list[str], *, limit: int = 3,
    ) -> list[tuple[str, float]]:
        """共现联想：与种子标签同现过的其他关联标签（共现次数×IDF 降序）。"""
        return await self._search.cooccurring_tags(seed_tags, limit=limit)

    async def extract_query_mentions(
        self, query: str, *, limit: int = 3,
    ) -> list[str]:
        """从查询文本识别已知实体/话题提及，返回对应标签。"""
        return await self._search.extract_query_mentions(query, limit=limit)

    async def expand_tag_seeds(
        self,
        seeds: list[str],
        *,
        graph_limit: int = 2,
        cooc_limit: int = 2,
    ) -> list[str]:
        """联想种子三层扩展：直接标签 → 图谱邻居 → 共现标签（保序去重）。

        图谱邻居：种子实体在关系图谱中的相邻节点（user:/group: 标签）；
        共现标签：与种子常一起出现的稀有标签（共现次数×IDF 排序）。
        """
        out = list(seeds)
        entity_seeds = [t for t in out if t.startswith(ENTITY_PREFIXES)]
        if entity_seeds:
            try:
                added = 0
                for edge in await self.graph.edges_for_scopes(entity_seeds, limit=10):
                    for endpoint in (edge["subject"], edge["object"]):
                        key = endpoint["node_key"]
                        if (key.startswith(ENTITY_PREFIXES) and key not in out
                                and added < graph_limit):
                            out.append(key)
                            added += 1
            except Exception as exc:
                log(f"图谱邻居种子扩展失败: {exc}", "DEBUG", tag="思维")
        try:
            for tag, _score in await self._search.cooccurring_tags(out, limit=cooc_limit):
                if tag not in out:
                    out.append(tag)
        except Exception as exc:
            log(f"共现种子扩展失败: {exc}", "DEBUG", tag="思维")
        return out

    async def search_by_tags(self, tags: list[str], limit: int = 20) -> list[MemoryEntry]:
        """按标签交集筛选记忆（返回包含所有指定标签的记忆）。"""
        return await self._search.search_by_tags(tags, limit)

    async def search_associative(
            self,
            tags: list[str],
            *,
            exclude_ids: Optional[set[int]] = None,
            limit: int = 5,
    ) -> list[tuple[MemoryEntry, float]]:
        """关联检索：查找与给定标签集合有任一交集的记忆（标签网络的一跳扩展）。"""
        return await self._search.search_associative(tags, exclude_ids=exclude_ids, limit=limit)

    async def search_fts(self, query: str, limit: int = 10) -> list[tuple[MemoryEntry, float]]:
        """FTS5 关键词搜索，返回 (entry, bm25_score) 列表。"""
        return await self._search.search_fts(query, limit)

    async def search_vector(
        self,
        query_vec: list[float],
        limit: int = 10,
        min_score: float = 0.3,
    ) -> list[tuple[MemoryEntry, float]]:
        """向量搜索：优先走 sqlite-vec ANN 索引，不可用时分批全表扫描。"""
        return await self._search.search_vector(query_vec, limit, min_score)

    async def search_hybrid(
        self,
        query: str,
        query_vec: Optional[list[float]] = None,
        query_tags: Optional[list[str]] = None,
        limit: int = 10,
        min_score: float = 0.1,
    ) -> list[tuple[MemoryEntry, float]]:
        """混合搜索：向量 + FTS 两路并行，取并集后统一评分。"""
        return await self._search.search_hybrid(
            query=query, query_vec=query_vec, query_tags=query_tags,
            limit=limit, min_score=min_score,
        )

    async def search_unified(
        self,
        query: str,
        query_vec: Optional[list[float]] = None,
        query_tags: Optional[list[str]] = None,
        limit: int = 10,
        min_score: float = 0.1,
        require_tags: Optional[list[str]] = None,
    ) -> list[MemorySearchResult]:
        """统一搜索：同时检索 memories 表和 chunks 表，合并排序返回。"""
        return await self._search.search_unified(
            query=query, query_vec=query_vec, query_tags=query_tags,
            limit=limit, min_score=min_score, require_tags=require_tags,
        )

    async def has_similar_content(self, content: str, min_overlap: float = 0.6) -> bool:
        """检查是否已存在语义相近的记忆（基于 FTS 候选 + bigram 相似度）。"""
        return await self._search.has_similar_content(content, min_overlap)

    # ------------------------------------------------------------------
    # 管理接口
    # ------------------------------------------------------------------

    async def list_all_with_id(
        self,
        memory_type: Optional[MemoryType] = None,
        limit: int = 200,
    ) -> list[Dict[str, Any]]:
        db = await self._get_db()
        sql = "SELECT id, type, content, source, importance, ts_ns, metadata_json, tags_json, access_count, last_accessed_ns FROM memories WHERE 1=1"
        params: list[Any] = []
        if memory_type:
            sql += " AND type=?"
            params.append(memory_type.value)
        sql += " ORDER BY ts_ns DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"], "type": r["type"], "content": r["content"],
                "source": r["source"], "importance": r["importance"], "ts_ns": r["ts_ns"],
                "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else {},
                "tags": json.loads(r["tags_json"]) if r["tags_json"] else [],
                "access_count": r["access_count"],
                "last_accessed_ns": r["last_accessed_ns"],
            }
            for r in rows
        ]

    async def list_paginated(
        self,
        page: int = 1,
        page_size: int = 50,
        memory_type: Optional[MemoryType] = None,
    ) -> Dict[str, Any]:
        """分页查询记忆，返回 {items, total, page, page_size, pages}。"""
        db = await self._get_db()
        total = await self.count(memory_type)
        offset = (max(1, page) - 1) * page_size

        sql = f"SELECT {_MEM_COLUMNS} FROM memories WHERE 1=1"
        params: list[Any] = []
        if memory_type:
            sql += " AND type=?"
            params.append(memory_type.value)
        sql += " ORDER BY ts_ns DESC LIMIT ? OFFSET ?"
        params.extend([page_size, offset])

        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()

        items = []
        for r in rows:
            entry = row_to_entry(r)
            items.append({
                "id": entry.id, "type": entry.memory_type.value,
                "content": entry.content, "source": entry.source,
                "importance": entry.importance, "timestamp": entry.timestamp,
                "tags": entry.tags, "access_count": entry.access_count,
            })

        pages = max(1, math.ceil(total / page_size))
        return {"items": items, "total": total, "page": page, "page_size": page_size, "pages": pages}

    async def get_type_counts(self) -> Dict[str, int]:
        """按类型统计记忆条目数量。"""
        db = await self._get_db()
        cursor = await db.execute("SELECT type, COUNT(*) as cnt FROM memories GROUP BY type")
        rows = await cursor.fetchall()
        return {r["type"]: r["cnt"] for r in rows}

    async def merge_memories(self, ids: list[int], merged_content: str, merged_type: Optional[MemoryType] = None) -> int:
        """将多条记忆合并为一条新记忆，旧记忆标记 importance=0（不删除）。返回新记忆 id。"""
        if not ids or not merged_content:
            return 0

        db = await self._get_db()

        # 获取原记忆信息用于继承标签和类型
        placeholders = ",".join("?" for _ in ids)
        cursor = await db.execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE id IN ({placeholders})",
            ids,
        )
        rows = await cursor.fetchall()
        if not rows:
            return 0

        # 合并标签（取并集）
        all_tags: set[str] = set()
        best_type = merged_type or MemoryType(rows[0]["type"])
        max_importance = 0.0
        for r in rows:
            try:
                tags = json.loads(r["tags_json"]) if r["tags_json"] else []
                all_tags.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass
            max_importance = max(max_importance, r["importance"])

        # 将旧记忆标记为已合并（importance=0）；记录原重要性用于失败补偿
        old_importance = {int(r["id"]): float(r["importance"]) for r in rows}
        async with self._tx(db):
            await db.execute(
                f"UPDATE memories SET importance = 0 WHERE id IN ({placeholders})",
                ids,
            )

        # 创建合并后的新记忆
        merged_tags = sorted(all_tags)
        merged_tags.append("merged")
        entry = MemoryEntry(
            memory_type=best_type,
            content=merged_content,
            source="merge",
            tags=merged_tags,
            importance=max_importance,
            metadata={"merged_from": ids},
        )
        try:
            new_id = await self.add(entry)
        except Exception:
            # 补偿：新记忆写入失败时恢复旧记忆重要性，
            # 避免旧记忆被全部检索路径过滤造成数据"假丢失"
            async with self._tx(db):
                for old_id, imp in old_importance.items():
                    await db.execute(
                        "UPDATE memories SET importance=? WHERE id=?", (imp, old_id),
                    )
            raise
        log(f"🔗 记忆合并: {ids} → id={new_id}", tag="思维")
        return new_id

    async def cleanup_low_importance(self, threshold: float = 0.05, max_age_hours: float = 24 * 90) -> int:
        """清理极低重要性老记忆：移入归档（软删除，可 restore），而非物理删除。

        与 forget_weak_memories 保持一致的归档语义；物理删除只发生在
        purge_archived_memories 的归档超期清理。
        """
        db = await self._get_db()
        cutoff_ts = int((time.time() - max_age_hours * 3600) * 1e9)
        ids_cursor = await db.execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE importance < ? AND ts_ns < ? AND type != ?",
            (threshold, cutoff_ts, MemoryType.PERMANENT.value),
        )
        entries = [row_to_entry(row) for row in await ids_cursor.fetchall()]
        async with self._tx(db):
            for entry in entries:
                await self._archive_entry(entry, "极低重要性清理")
        return len(entries)

    # ------------------------------------------------------------------
    # 健康状态
    # ------------------------------------------------------------------

    async def get_health_status(self) -> Dict[str, Any]:
        """返回记忆系统综合健康状态。"""
        type_counts = await self.get_type_counts()
        index_status = await self.get_index_status()
        total = sum(type_counts.values())

        warn_threshold: int = _get_memory_config_value("memory_warn_threshold", 200)
        max_per_type: int = _get_memory_config_value("memory_max_per_type", 500)

        warnings: list[str] = []
        for mem_type, cnt in type_counts.items():
            if cnt >= max_per_type:
                warnings.append(f"{mem_type} 已达上限 ({cnt}/{max_per_type})，建议合并")
            elif cnt >= warn_threshold:
                warnings.append(f"{mem_type} 数量较多 ({cnt}/{warn_threshold})，建议整理")

        return {
            "total_memories": total,
            "type_counts": type_counts,
            "warn_threshold": warn_threshold,
            "max_per_type": max_per_type,
            "warnings": warnings,
            "has_warnings": len(warnings) > 0,
            **index_status,
        }

    # ------------------------------------------------------------------
    # 文件/chunks 索引与 embedding 缓存（委托 FileIndexStore）
    # ------------------------------------------------------------------

    async def get_file(self, path: str) -> Optional[Dict[str, Any]]:
        return await self._files.get_file(path)

    async def upsert_file(self, path: str, hash_val: str, mtime_ns: int, size: int) -> None:
        await self._files.upsert_file(path, hash_val, mtime_ns, size)

    async def delete_file(self, path: str) -> None:
        """删除文件记录及其所有 chunks。"""
        await self._files.delete_file(path)

    async def list_files(self) -> list[Dict[str, Any]]:
        return await self._files.list_files()

    async def list_chunk_counts(self) -> Dict[str, int]:
        """返回每个已索引文件的 chunk 数量（path → count）。"""
        return await self._files.list_chunk_counts()

    async def upsert_chunks(self, chunks: list[Dict[str, Any]]) -> int:
        """批量写入 chunks（id 冲突时替换）。返回写入数量。"""
        return await self._files.upsert_chunks(chunks)

    async def delete_chunks_by_path(self, path: str) -> int:
        return await self._files.delete_chunks_by_path(path)

    async def search_chunks_vector(
        self,
        query_vec: list[float],
        limit: int = 10,
        min_score: float = 0.3,
    ) -> list[Dict[str, Any]]:
        """在 chunks 表中执行向量搜索：优先 sqlite-vec 索引，降级分批全表扫描。"""
        return await self._files.search_chunks_vector(query_vec, limit, min_score)

    async def search_chunks_fts(self, query: str, limit: int = 10) -> list[Dict[str, Any]]:
        """在 chunks_fts 中执行全文搜索。"""
        return await self._files.search_chunks_fts(query, limit)

    async def get_index_status(self) -> Dict[str, Any]:
        """返回文件索引的统计信息。"""
        return await self._files.get_index_status()

    async def get_cached_embedding(self, text_hash: str) -> Optional[list[float]]:
        return await self._files.get_cached_embedding(text_hash)

    async def put_cached_embedding(self, text_hash: str, vec: list[float]) -> None:
        await self._files.put_cached_embedding(text_hash, vec)

    async def clean_embedding_cache(self) -> int:
        """清理不再被 chunks 引用的过期 embedding 缓存。"""
        return await self._files.clean_embedding_cache()

    async def purge_audit_log(self, older_than_days: int = 30) -> int:
        """清理过期审计日志（表只追加，不清理会让每 tick 的汇总查询线性恶化）。"""
        db = await self._get_db()
        cutoff = int((time.time() - older_than_days * 86400) * 1e9)
        async with self._tx(db):
            cursor = await db.execute(
                "DELETE FROM memory_audit WHERE ts_ns < ?", (cutoff,),
            )
        return int(cursor.rowcount or 0)

    # ------------------------------------------------------------------
    # Embedding 管理
    # ------------------------------------------------------------------

    async def count_pending_embeddings(self) -> Dict[str, int]:
        """统计待后台 worker 回填向量的行数（memories / chunks）。"""
        db = await self._get_db()
        mem = (await (await db.execute(
            "SELECT COUNT(*) as cnt FROM memories WHERE embedding_blob IS NULL"
        )).fetchone())["cnt"]
        chunks = (await (await db.execute(
            "SELECT COUNT(*) as cnt FROM chunks WHERE embedding IS NULL"
        )).fetchone())["cnt"]
        return {"memories": mem, "chunks": chunks}

    async def rebuild_embeddings(self) -> Dict[str, int]:
        """清空全部向量数据（记忆 / chunk / 缓存 + vec 索引），供切换 embedding 模型后重建。

        权威 BLOB 列置 NULL 后由后台 EmbeddingWorker 按新模型重新回填；
        vec 索引表直接删除，维度在首次写入时按新向量惰性重建。
        """
        db = await self._get_db()
        mem = (await (await db.execute(
            "SELECT COUNT(*) as cnt FROM memories WHERE embedding_blob IS NOT NULL"
        )).fetchone())["cnt"]
        chunks = (await (await db.execute(
            "SELECT COUNT(*) as cnt FROM chunks WHERE embedding IS NOT NULL"
        )).fetchone())["cnt"]
        async with self._tx(db):
            await db.execute("UPDATE memories SET embedding_blob=NULL")
            await db.execute("UPDATE chunks SET embedding=NULL")
            await db.execute("DELETE FROM embedding_cache")
            await db.execute("DROP TABLE IF EXISTS memories_vec")
            await db.execute("DROP TABLE IF EXISTS chunks_vec")
        self._vec_dims = None
        return {"memories": mem, "chunks": chunks}

    # ------------------------------------------------------------------
    # 工具错误追踪（委托 ToolErrorTracker）
    # ------------------------------------------------------------------

    async def record_tool_error(
        self,
        tool_name: str,
        error_type: str,
        error_msg: str,
        args_json: str = "{}",
        context: str = "",
    ) -> Optional[int]:
        """记录工具执行错误，返回记录 ID。"""
        return await self._tool_errors.record(tool_name, error_type, error_msg, args_json, context)

    async def get_tool_errors(
        self,
        tool_name: str = "",
        limit: int = 20,
        unresolved_only: bool = False,
    ) -> list[Dict[str, Any]]:
        """查询工具错误历史。"""
        return await self._tool_errors.get_errors(tool_name, limit, unresolved_only)

    async def get_tool_error_stats(self) -> list[Dict[str, Any]]:
        """按工具名统计错误次数。"""
        return await self._tool_errors.get_stats()

    async def resolve_tool_error(self, error_id: int) -> bool:
        """标记工具错误为已解决。"""
        return await self._tool_errors.resolve(error_id)
