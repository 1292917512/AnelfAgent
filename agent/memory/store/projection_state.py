"""派生索引的持久化构建状态、权威快照与覆盖审计，不调用外部后端。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

from ..graph.store import GraphStore
from ..projection import PROJECTION_VERSION, memory_is_projectable
from ._shared import MEM_COLUMNS, entry_projection_payload, projection_content_hash, row_to_entry
from .cognee_queue import CogneeSyncQueue
from .connection import MemoryConnectionManager

_STATE_KEY = "cognee_projection"


class ProjectionState(BaseModel):
    """格式与构建代际独立于依赖版本；旧数据集保留至人工清理。"""

    version: int = PROJECTION_VERSION
    phase: Literal["required", "requested", "scanning", "building", "verifying", "ready", "failed"] = "required"
    rebuild_requested: bool = False
    root: str = ""
    base_prefix: str = ""
    namespace: str = ""
    previous_namespaces: list[str] = Field(default_factory=list)
    memory_cursor: int = 0
    graph_cursor: int = 0
    memory_scanned: bool = False
    graph_scanned: bool = False
    backup_path: str = ""
    counts: dict[str, int] = Field(default_factory=dict)
    verified: int = 0
    legacy_preserved: int = 0
    error: str = ""
    started_at: float = 0
    completed_at: float = 0


class ProjectionStateStore:
    """复用记忆库连接与写锁，状态和映射切换作为同一事务提交。"""

    def __init__(self, conn: MemoryConnectionManager, queue: CogneeSyncQueue, graph: GraphStore) -> None:
        self._conn = conn
        self._queue = queue
        self._graph = graph

    async def load(self) -> ProjectionState | None:
        db = await self._conn.get_db()
        row = await (await db.execute("SELECT value FROM schema_meta WHERE key=?", (_STATE_KEY,))).fetchone()
        return ProjectionState.model_validate_json(row[0]) if row else None

    async def save(self, state: ProjectionState) -> None:
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            await self._save(db, state)

    @staticmethod
    async def _save(db: aiosqlite.Connection, state: ProjectionState) -> None:
        await db.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES(?,?)",
                         (_STATE_KEY, state.model_dump_json()))

    async def begin(self, state: ProjectionState, backup: Path) -> None:
        """先完整备份权威 SQLite，再原子切换队列/映射；不修改原始内容表。"""
        db = await self._conn.get_db()
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.touch(exist_ok=False)
        # 单连接备份必须在无写事务时执行，持锁隔离其他业务事务。
        async with self._conn.write_lock:
            async with aiosqlite.connect(str(backup)) as target:
                task = asyncio.create_task(db.backup(target))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task
                    raise
                row = await (await target.execute("PRAGMA quick_check")).fetchone()
                if row is None or row[0] != "ok":
                    raise RuntimeError("原生记忆快照完整性校验失败")
        next_state = state.model_copy(update={
            "backup_path": str(backup), "phase": "scanning",
            "counts": await self._queue.eligible_counts(),
        })
        async with self._conn.tx(db):
            await db.execute("DELETE FROM cognee_sync_queue")
            await db.execute("DELETE FROM cognee_entry_map")
            await db.execute("DELETE FROM cognee_projection_attempts")
            await self._save(db, next_state)
        state.backup_path, state.phase, state.counts = next_state.backup_path, next_state.phase, next_state.counts

    async def scan_page(self, state: ProjectionState, limit: int = 100) -> ProjectionState:
        """崩溃后最多重复入队一页；指纹与稳定 ID 保证重试不会复制文档。"""
        kind = "memory" if not state.memory_scanned else "graph_node"
        cursor = state.memory_cursor if kind == "memory" else state.graph_cursor
        page = await self._queue.backfill_page(kind, cursor, limit)
        if kind == "memory":
            state.memory_cursor = page["after"]
            state.memory_scanned = page["scanned"] < limit
        else:
            state.graph_cursor = page["after"]
            state.graph_scanned = page["scanned"] < limit
        if state.memory_scanned and state.graph_scanned:
            state.phase = "building"
        await self.save(state)
        return state

    async def mappings(self) -> list[dict[str, Any]]:
        db = await self._conn.get_db()
        return [dict(r) for r in await (await db.execute("SELECT * FROM cognee_entry_map")).fetchall()]

    async def preserved_sources(self) -> set[tuple[str, int]]:
        """映射必须能回到权威内容或明确的删除记录，不能仅凭旧映射认定无损。"""
        db = await self._conn.get_db()
        rows = await (await db.execute(
            "SELECT 'memory' AS kind,id FROM memories UNION SELECT 'memory',id FROM memories_archive "
            "UNION SELECT 'memory',memory_id FROM memories_tombstone UNION SELECT 'graph_node',id FROM graph_nodes "
            "UNION SELECT entry_kind,entry_id FROM cognee_sync_queue WHERE operation='delete'"
        )).fetchall()
        return {(str(row[0]), int(row[1])) for row in rows}

    async def legacy_source(self, kind: str, entry_id: int) -> dict[str, Any] | None:
        """读取原始身份，不沿合并链替换 ID，也不把已归档记录重新激活。"""
        db = await self._conn.get_db()
        return await self._legacy_source(db, kind, entry_id)

    @staticmethod
    async def _legacy_source(db: aiosqlite.Connection, kind: str, entry_id: int) -> dict[str, Any] | None:
        if kind == "graph_node":
            row = await (await db.execute(
                "SELECT node_key,node_type FROM graph_nodes WHERE id=?", (entry_id,),
            )).fetchone()
        elif kind == "memory":
            row = await (await db.execute(
                "SELECT type,content,'active' AS status FROM memories WHERE id=? "
                "UNION ALL SELECT type,content,'archived' FROM memories_archive WHERE id=? "
                "UNION ALL SELECT type,NULL,'tombstone' FROM memories_tombstone WHERE memory_id=? LIMIT 1",
                (entry_id, entry_id, entry_id),
            )).fetchone()
        else:
            return None
        return dict(row) if row else None

    async def preserve_legacy_document(
        self, *, dataset_id: str, data_id: str, kind: str, entry_id: int,
        classification: str, document: dict[str, Any],
    ) -> None:
        """只追加历史原文及元数据；重试幂等，读回校验后才允许切换派生映射。"""
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            if await self._legacy_source(db, kind, entry_id) is None:
                raise RuntimeError(f"旧文档的原生来源已消失: {kind}:{entry_id}")
            await db.execute(
                "INSERT OR IGNORE INTO cognee_legacy_documents VALUES(?,?,?,?,?,?,?,?)",
                (dataset_id, data_id, digest, kind, entry_id, classification, payload, time.time_ns()),
            )
            row = await (await db.execute(
                "SELECT document_json FROM cognee_legacy_documents "
                "WHERE dataset_id=? AND data_id=? AND snapshot_hash=?", (dataset_id, data_id, digest),
            )).fetchone()
            if row is None or row[0] != payload:
                raise RuntimeError(f"旧文档留存校验失败: {data_id}")

    async def stage(self, kind: str, entry_id: int, dataset: str, data_id: str) -> None:
        """网络写入前记录稳定目标；异常重试或内容变化后仍能清理未提交副本。"""
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            await db.execute("INSERT OR IGNORE INTO cognee_projection_attempts VALUES(?,?,?,?)",
                             (kind, entry_id, dataset, data_id))

    async def staged(self, kind: str = "", entry_id: int = 0) -> list[dict[str, Any]]:
        db = await self._conn.get_db()
        sql = "SELECT * FROM cognee_projection_attempts"
        cursor = await db.execute(sql + " WHERE entry_kind=? AND entry_id=?", (kind, entry_id)) if kind else await db.execute(sql)
        return [dict(row) for row in await cursor.fetchall()]

    @staticmethod
    async def _revision(db: aiosqlite.Connection) -> int:
        row = await (await db.execute("SELECT value FROM schema_meta WHERE key='projection_revision'")).fetchone()
        if row is None:
            raise RuntimeError("缺少投影审计版本，拒绝切换索引")
        return int(row[0])

    async def audit(self) -> dict[str, Any]:
        """锁外核对逐条指纹；两端版本相同才是可用于切换的一致审计。"""
        db = await self._conn.get_db()
        async with self._conn.write_lock:
            revision = await self._revision(db)
        expected: dict[tuple[str, int], str] = {}
        rows = await (await db.execute(f"SELECT {MEM_COLUMNS} FROM memories")).fetchall()
        for row in rows:
            entry_id = int(row["id"])
            payload = entry_projection_payload(row_to_entry(row), entry_id)
            if memory_is_projectable(payload):
                expected[("memory", entry_id)] = projection_content_hash(payload)
        nodes = await (await db.execute("SELECT id FROM graph_nodes WHERE archived=0")).fetchall()
        for row in nodes:
            projection = await self._graph.render_node_projection(int(row["id"]))
            if projection is not None:
                expected[("graph_node", int(row["id"]))] = projection[1]
        mappings = {(str(r["entry_kind"]), int(r["entry_id"])): r for r in await self.mappings()}
        mismatches = [f"{kind}:{entry_id}" for (kind, entry_id), fingerprint in expected.items()
                      if (kind, entry_id) not in mappings
                      or mappings[(kind, entry_id)]["content_hash"] != fingerprint]
        extra = [f"{kind}:{entry_id}" for kind, entry_id in mappings.keys() - expected.keys()]
        status = await self._queue.get_status()
        async with self._conn.write_lock:
            consistent = revision == await self._revision(db)
        return {"revision": revision, "consistent": consistent, "expected": len(expected), "mapped": len(mappings), "missing_or_stale": mismatches,
                "extra": extra, "pending": status["pending"], "failed": status["failed"]}

    async def finish(self, state: ProjectionState, verified: int, *, revision: int) -> bool:
        """以常数时间持锁比较审计版本；后端验收期间的任何新写入都会推迟切换。"""
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            if revision != await self._revision(db):
                return False
            state.phase = "ready"
            state.verified = verified
            state.error = ""
            state.completed_at = time.time()
            await self._save(db, state)
        return True
