"""Cognee 异步投影队列：记忆/关系节点变更 → cognee 知识图谱的持久化 outbox。

写路径在业务事务内调用 enqueue_sync 压缩入队；后台 worker 经
claim_batch / complete / fail 消费；reset/retry/backfill 供管理接口使用。
连接与事务均由 MemoryConnectionManager 提供，本模块不自建连接。

条目类型（entry_kind）：memory（记忆，entry_id=memories.id）/
graph_node（关系图谱节点，entry_id=graph_nodes.id）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import aiosqlite

from core.log import log

from ..projection import memory_is_projectable
from ._shared import (
    MEM_COLUMNS,
    entry_projection_payload,
    projection_content_hash,
    row_to_entry,
)
from .connection import MemoryConnectionManager, fetch_count

# 投影条目类型
ENTRY_KIND_MEMORY = "memory"
ENTRY_KIND_GRAPH_NODE = "graph_node"


class CogneeSyncQueue:
    """cognee_sync_queue + cognee_entry_map 两张表的全部读写。"""

    def __init__(self, conn: MemoryConnectionManager) -> None:
        self._conn = conn
        self._projection_enabled = False

    def set_enabled(self, enabled: bool) -> None:
        """启用或禁用 Cognee 持久化投影队列。"""
        self._projection_enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._projection_enabled

    async def enqueue_sync(
        self,
        db: aiosqlite.Connection,
        entry_id: int,
        operation: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        entry_kind: str = ENTRY_KIND_MEMORY,
        force: bool = False,
    ) -> None:
        """在当前事务中追加 Cognee 投影操作，并压缩尚未执行的旧操作。

        内容未变的 upsert 在此直接跳过（指纹与上次成功同步一致 =
        投影已是最新），避免 importance 漂移等无效变更逐条重跑图谱
        抽取；backfill 等显式修复路径经 force=True 绕过判重。
        """
        if not self._projection_enabled or entry_id <= 0:
            return
        if entry_kind == ENTRY_KIND_MEMORY:
            # 来源标签/私密度属于关系文档的一部分，与原始变更同事务入队。
            rows = await (await db.execute(
                "SELECT subject_id AS id FROM graph_edges WHERE source_memory_id=? AND archived=0 "
                "UNION SELECT object_id AS id FROM graph_edges WHERE source_memory_id=? AND archived=0",
                (entry_id, entry_id),
            )).fetchall()
            for row in rows:
                await self.enqueue_sync(db, int(row["id"]), "upsert", {"node_id": int(row["id"])},
                                        entry_kind=ENTRY_KIND_GRAPH_NODE)
        if (
            operation == "upsert"
            and entry_kind == ENTRY_KIND_MEMORY
            and payload
            and not memory_is_projectable(payload)
        ):
            # 计划、已失效或已合并条目退出活跃图谱；在途任务也必须后续删除。
            cursor = await db.execute(
                "SELECT data_id FROM cognee_entry_map WHERE entry_kind=? AND entry_id=?",
                (entry_kind, entry_id),
            )
            mapping_row = await cursor.fetchone()
            staged = await (await db.execute(
                "SELECT 1 FROM cognee_projection_attempts WHERE entry_kind=? AND entry_id=? LIMIT 1",
                (entry_kind, entry_id),
            )).fetchone()
            processing = await (await db.execute(
                "SELECT 1 FROM cognee_sync_queue WHERE entry_kind=? AND entry_id=? "
                "AND status='processing' LIMIT 1", (entry_kind, entry_id),
            )).fetchone()
            if (not mapping_row or not mapping_row["data_id"]) and processing is None and staged is None:
                await db.execute(
                    "DELETE FROM cognee_sync_queue "
                    "WHERE entry_kind=? AND entry_id=? AND status IN ('pending', 'failed')",
                    (entry_kind, entry_id),
                )
                return
            operation, payload = "delete", None
        if (
            operation == "upsert"
            and not force
            and entry_kind == ENTRY_KIND_MEMORY
            and payload
            and await self._already_projected(db, entry_id, payload)
        ):
            # 状态已同步：作废残留的待执行条目后不再入队
            await db.execute(
                "DELETE FROM cognee_sync_queue "
                "WHERE entry_kind=? AND entry_id=? AND status IN ('pending', 'failed')",
                (entry_kind, entry_id),
            )
            return
        now_ns = time.time_ns()
        if force and entry_kind == ENTRY_KIND_GRAPH_NODE:
            payload = {**(payload or {}), "force": True}
        await db.execute(
            "DELETE FROM cognee_sync_queue "
            "WHERE entry_kind=? AND entry_id=? AND status IN ('pending', 'failed')",
            (entry_kind, entry_id),
        )
        await db.execute(
            "INSERT INTO cognee_sync_queue"
            "(entry_kind, entry_id, operation, payload_json, status, attempts, next_retry_ns, "
            "last_error, created_ns, updated_ns) VALUES(?,?,?,?,'pending',0,0,'',?,?)",
            (
                entry_kind,
                entry_id,
                operation,
                json.dumps(payload or {}, ensure_ascii=False),
                now_ns,
                now_ns,
            ),
        )

    async def _already_projected(
        self,
        db: aiosqlite.Connection,
        entry_id: int,
        payload: Dict[str, Any],
    ) -> bool:
        """upsert 负载与上次成功同步的指纹一致时视为已投影。

        在途批次（processing）持有更新版本的负载并会在完成时覆写映射，
        此时不得跳过——内容回退到已同步版本的入队必须照常排队。
        """
        fingerprint = projection_content_hash(payload)
        if not fingerprint:
            return False
        if await (await db.execute(
            "SELECT 1 FROM cognee_projection_attempts WHERE entry_kind=? AND entry_id=? LIMIT 1",
            (ENTRY_KIND_MEMORY, entry_id),
        )).fetchone():
            return False
        cursor = await db.execute(
            "SELECT content_hash FROM cognee_entry_map "
            "WHERE entry_kind=? AND entry_id=?",
            (ENTRY_KIND_MEMORY, entry_id),
        )
        row = await cursor.fetchone()
        if not row or str(row["content_hash"] or "") != fingerprint:
            return False
        cursor = await db.execute(
            "SELECT 1 FROM cognee_sync_queue "
            "WHERE entry_kind=? AND entry_id=? AND status='processing' LIMIT 1",
            (ENTRY_KIND_MEMORY, entry_id),
        )
        return await cursor.fetchone() is None

    async def claim_batch(self, limit: int) -> list[Dict[str, Any]]:
        """领取一批可执行投影任务，避免同进程重复消费。"""
        db = await self._conn.get_db()
        now_ns = time.time_ns()
        async with self._conn.tx(db):
            cursor = await db.execute(
                "SELECT id, entry_kind, entry_id, operation, payload_json, attempts "
                "FROM cognee_sync_queue WHERE status='pending' AND next_retry_ns<=? "
                "ORDER BY id LIMIT ?", (now_ns, max(1, limit)),
            )
            rows = await cursor.fetchall()
            if not rows:
                return []
            ids = [int(row["id"]) for row in rows]
            placeholders = ",".join("?" for _ in ids)
            await db.execute(
                f"UPDATE cognee_sync_queue SET status='processing', updated_ns=? "
                f"WHERE status='pending' AND id IN ({placeholders})",
                (now_ns, *ids),
            )
        result: list[Dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            result.append({
                "queue_id": int(row["id"]),
                "entry_kind": str(row["entry_kind"]),
                "entry_id": int(row["entry_id"]),
                "operation": str(row["operation"]),
                "payload": payload,
                "attempts": int(row["attempts"]),
            })
        return result

    async def requeue_stale(self, stale_after_seconds: float = 900.0) -> int:
        """回收卡死的投影任务：processing 超过阈值时间的重置为 pending。

        worker 认领后若进程退出或任务被取消，条目会永远卡在 processing。
        本方法将其重新入队（不增加 attempts——并非执行失败而是被中断）。
        """
        db = await self._conn.get_db()
        cutoff_ns = time.time_ns() - int(stale_after_seconds * 1e9)
        async with self._conn.tx(db):
            cursor = await db.execute(
                "UPDATE cognee_sync_queue SET status='pending', updated_ns=? "
                "WHERE status='processing' AND updated_ns<?",
                (time.time_ns(), cutoff_ns),
            )
        requeued = cursor.rowcount or 0
        if requeued:
            log(f"Cognee 投影队列回收卡死任务: {requeued} 条", "WARNING", tag="思维")
        return requeued

    async def complete(
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
        documents_json: str = "[]",
    ) -> None:
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            # 先删队列条目并确认其存在：reset 已清空队列时
            # rowcount 为 0，跳过映射写入，防止在途 worker 复活刚清掉的映射
            cursor = await db.execute(
                "DELETE FROM cognee_sync_queue WHERE id=?", (queue_id,),
            )
            if (cursor.rowcount or 0) == 0:
                return
            if delete_mapping:
                await db.execute(
                    "DELETE FROM cognee_entry_map WHERE entry_kind=? AND entry_id=?",
                    (entry_kind, entry_id),
                )
            elif dataset_name:
                await db.execute(
                    "INSERT OR REPLACE INTO cognee_entry_map"
                    "(entry_kind, entry_id, dataset_name, dataset_id, data_id, synced_ns, "
                    "content_hash, documents_json) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        entry_kind, entry_id, dataset_name, dataset_id, data_id,
                        time.time_ns(), content_hash, documents_json,
                    ),
                )
            if delete_mapping or dataset_name:
                await db.execute("DELETE FROM cognee_projection_attempts WHERE entry_kind=? AND entry_id=?",
                                 (entry_kind, entry_id))

    async def fail(
        self,
        queue_id: int,
        error: str,
        *,
        max_retries: int,
        retry_delay_seconds: float,
    ) -> None:
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT attempts FROM cognee_sync_queue WHERE id=?", (queue_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return
        attempts = int(row["attempts"]) + 1
        status = "failed" if attempts >= max_retries else "pending"
        next_retry_ns = time.time_ns() + int(max(0.5, retry_delay_seconds) * 1e9)
        async with self._conn.tx(db):
            await db.execute(
                "UPDATE cognee_sync_queue SET status=?, attempts=?, next_retry_ns=?, "
                "last_error=?, updated_ns=? WHERE id=?",
                (status, attempts, next_retry_ns, error[:1000], time.time_ns(), queue_id),
            )

    async def get_mapping(
        self,
        entry_id: int,
        *,
        entry_kind: str = ENTRY_KIND_MEMORY,
    ) -> Optional[Dict[str, Any]]:
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT entry_kind, entry_id, dataset_name, dataset_id, data_id, synced_ns, "
            "content_hash, documents_json FROM cognee_entry_map WHERE entry_kind=? AND entry_id=?",
            (entry_kind, entry_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_status(self) -> Dict[str, int]:
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT status, COUNT(*) AS cnt FROM cognee_sync_queue GROUP BY status"
        )
        counts = {str(row["status"]): int(row["cnt"]) for row in await cursor.fetchall()}
        mapped = await db.execute("SELECT COUNT(*) AS cnt FROM cognee_entry_map")
        mapped_row = await mapped.fetchone()
        return {
            "pending": counts.get("pending", 0) + counts.get("processing", 0),
            "failed": counts.get("failed", 0),
            "synced": int(mapped_row["cnt"]) if mapped_row else 0,
        }

    async def activity(self) -> dict[str, Any]:
        """持久队列的在途、重试与最近未解决错误；进程重启仍可观测。"""
        db = await self._conn.get_db()
        row = await (await db.execute(
            "SELECT COUNT(CASE WHEN status='processing' THEN 1 END) AS processing, "
            "COUNT(CASE WHEN attempts>0 AND status IN ('pending','processing') THEN 1 END) AS retrying, "
            "MIN(CASE WHEN status='pending' AND attempts>0 THEN next_retry_ns END) AS next_retry_ns "
            "FROM cognee_sync_queue"
        )).fetchone()
        error = await (await db.execute(
            "SELECT last_error FROM cognee_sync_queue WHERE last_error<>'' ORDER BY updated_ns DESC LIMIT 1"
        )).fetchone()
        return {
            "processing": int(row["processing"]) if row else 0,
            "retrying": int(row["retrying"]) if row else 0,
            "next_retry_at": (int(row["next_retry_ns"] or 0) / 1e9) if row else 0,
            "last_error": str(error[0]) if error else "",
        }

    async def reset(self) -> Dict[str, int]:
        """清空 cognee 投影队列与 ID 映射（清空重建 cognee 数据前调用）。

        映射指向已被 prune 的 cognee 数据，不清理会导致重建时对陈旧 ID
        执行删除而产生批量伪失败；队列清零使状态计数从新一轮重建起算。
        """
        db = await self._conn.get_db()
        queued = await fetch_count(db, "SELECT COUNT(*) FROM cognee_sync_queue")
        mapped = await fetch_count(db, "SELECT COUNT(*) FROM cognee_entry_map")
        async with self._conn.tx(db):
            await db.execute("DELETE FROM cognee_sync_queue")
            await db.execute("DELETE FROM cognee_entry_map")
            await db.execute("DELETE FROM cognee_projection_attempts")
        return {"queue": queued, "mappings": mapped}

    async def retry_failed(self) -> int:
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            cursor = await db.execute(
                "UPDATE cognee_sync_queue SET status='pending', attempts=0, "
                "next_retry_ns=0, last_error='', updated_ns=? WHERE status='failed'",
                (time.time_ns(),),
            )
        return cursor.rowcount or 0

    async def enqueue_backfill(self, *, limit: int = 0) -> int:
        """回填活跃记忆与关系节点；原始归档、计划、合并行不进入活跃索引。"""
        if not self._projection_enabled:
            return 0
        count = 0
        for kind in (ENTRY_KIND_MEMORY, ENTRY_KIND_GRAPH_NODE):
            after = 0
            while limit <= 0 or count < limit:
                size = min(100, limit - count) if limit > 0 else 100
                page = await self.backfill_page(kind, after, size)
                count += page["queued"]
                after = page["after"]
                if page["scanned"] < size:
                    break
        return count

    async def backfill_page(self, kind: str, after: int, limit: int = 100) -> dict[str, int]:
        """按主键分页，读取与入队共用事务，避免旧快照覆盖并发更新。"""
        if kind not in (ENTRY_KIND_MEMORY, ENTRY_KIND_GRAPH_NODE):
            raise ValueError("未知投影类型")
        if not self.enabled:
            raise RuntimeError("投影变更记录未启用")
        db = await self._conn.get_db()
        queued = 0
        async with self._conn.tx(db):
            sql = (f"SELECT {MEM_COLUMNS} FROM memories WHERE id>? ORDER BY id LIMIT ?"
                   if kind == ENTRY_KIND_MEMORY else
                   "SELECT id FROM graph_nodes WHERE archived=0 AND id>? ORDER BY id LIMIT ?")
            rows = list(await (await db.execute(sql, (after, max(1, limit)))).fetchall())
            for row in rows:
                after = int(row["id"])
                payload = (entry_projection_payload(row_to_entry(row), after)
                           if kind == ENTRY_KIND_MEMORY else {"node_id": after})
                if kind == ENTRY_KIND_MEMORY and not memory_is_projectable(payload):
                    continue
                await self.enqueue_sync(db, after, "upsert", payload, entry_kind=kind, force=True)
                queued += 1
        return {"after": after, "scanned": len(rows), "queued": queued}

    async def eligible_counts(self) -> dict[str, int]:
        """预览与实际回填使用同一资格判定。"""
        db = await self._conn.get_db()
        rows = list(await (await db.execute(f"SELECT {MEM_COLUMNS} FROM memories")).fetchall())
        memories = sum(memory_is_projectable(entry_projection_payload(row_to_entry(r), int(r["id"])))
                       for r in rows)
        graph = await fetch_count(db, "SELECT COUNT(*) FROM graph_nodes WHERE archived=0")
        return {"memories": memories, "graph_nodes": graph, "excluded_memories": len(rows) - memories}
