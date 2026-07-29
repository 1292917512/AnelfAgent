"""Cognee 异步投影队列：记忆变更 → cognee 知识图谱的持久化 outbox。

写路径在业务事务内调用 enqueue_sync 压缩入队；后台 worker 经
claim_batch / complete / fail 消费；reset/retry/backfill 供管理接口使用。
连接与事务均由 MemoryConnectionManager 提供，本模块不自建连接。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import aiosqlite

from core.log import log

from ._shared import MEM_COLUMNS, entry_projection_payload, row_to_entry
from .connection import MemoryConnectionManager


class CogneeSyncQueue:
    """cognee_sync_queue + cognee_memory_map 两张表的全部读写。"""

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
        memory_id: int,
        operation: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """在当前事务中追加 Cognee 投影操作，并压缩尚未执行的旧操作。"""
        if not self._projection_enabled or memory_id <= 0:
            return
        now_ns = time.time_ns()
        await db.execute(
            "DELETE FROM cognee_sync_queue "
            "WHERE memory_id=? AND status IN ('pending', 'failed')",
            (memory_id,),
        )
        await db.execute(
            "INSERT INTO cognee_sync_queue"
            "(memory_id, operation, payload_json, status, attempts, next_retry_ns, "
            "last_error, created_ns, updated_ns) VALUES(?,?,?,'pending',0,0,'',?,?)",
            (
                memory_id,
                operation,
                json.dumps(payload or {}, ensure_ascii=False),
                now_ns,
                now_ns,
            ),
        )

    async def claim_batch(self, limit: int) -> list[Dict[str, Any]]:
        """领取一批可执行投影任务，避免同进程重复消费。"""
        db = await self._conn.get_db()
        now_ns = time.time_ns()
        cursor = await db.execute(
            "SELECT id, memory_id, operation, payload_json, attempts "
            "FROM cognee_sync_queue "
            "WHERE status='pending' AND next_retry_ns<=? ORDER BY id LIMIT ?",
            (now_ns, max(1, limit)),
        )
        rows = await cursor.fetchall()
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        async with self._conn.tx(db):
            await db.execute(
                f"UPDATE cognee_sync_queue SET status='processing', updated_ns=? "
                f"WHERE id IN ({placeholders})",
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
                "memory_id": int(row["memory_id"]),
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
        memory_id: int,
        *,
        dataset_name: str = "",
        dataset_id: str = "",
        data_id: str = "",
        delete_mapping: bool = False,
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
                    "DELETE FROM cognee_memory_map WHERE memory_id=?", (memory_id,),
                )
            elif dataset_name:
                await db.execute(
                    "INSERT OR REPLACE INTO cognee_memory_map"
                    "(memory_id, dataset_name, dataset_id, data_id, synced_ns) "
                    "VALUES(?,?,?,?,?)",
                    (memory_id, dataset_name, dataset_id, data_id, time.time_ns()),
                )

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

    async def get_mapping(self, memory_id: int) -> Optional[Dict[str, Any]]:
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT memory_id, dataset_name, dataset_id, data_id, synced_ns "
            "FROM cognee_memory_map WHERE memory_id=?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_status(self) -> Dict[str, int]:
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT status, COUNT(*) AS cnt FROM cognee_sync_queue GROUP BY status"
        )
        counts = {str(row["status"]): int(row["cnt"]) for row in await cursor.fetchall()}
        mapped = await db.execute("SELECT COUNT(*) AS cnt FROM cognee_memory_map")
        mapped_row = await mapped.fetchone()
        return {
            "pending": counts.get("pending", 0) + counts.get("processing", 0),
            "failed": counts.get("failed", 0),
            "synced": int(mapped_row["cnt"]) if mapped_row else 0,
        }

    async def reset(self) -> Dict[str, int]:
        """清空 cognee 投影队列与 ID 映射（清空重建 cognee 数据前调用）。

        映射指向已被 prune 的 cognee 数据，不清理会导致重建时对陈旧 ID
        执行删除而产生批量伪失败；队列清零使状态计数从新一轮重建起算。
        """
        db = await self._conn.get_db()
        queued = (await (await db.execute(
            "SELECT COUNT(*) AS c FROM cognee_sync_queue"
        )).fetchone())["c"]
        mapped = (await (await db.execute(
            "SELECT COUNT(*) AS c FROM cognee_memory_map"
        )).fetchone())["c"]
        async with self._conn.tx(db):
            await db.execute("DELETE FROM cognee_sync_queue")
            await db.execute("DELETE FROM cognee_memory_map")
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
        """显式将历史记忆加入投影队列；不会在启动时自动调用。"""
        if not self._projection_enabled:
            return 0
        db = await self._conn.get_db()
        sql = f"SELECT {MEM_COLUMNS} FROM memories ORDER BY id"
        params: tuple[Any, ...] = ()
        if limit > 0:
            sql += " LIMIT ?"
            params = (limit,)
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        async with self._conn.tx(db):
            for row in rows:
                entry = row_to_entry(row)
                if entry.id:
                    await self.enqueue_sync(
                        db,
                        entry.id,
                        "upsert",
                        entry_projection_payload(entry, entry.id),
                    )
        return len(rows)
