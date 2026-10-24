"""对话历史检索：限定会话和时间窗口，复用精确批量向量评分。

模型看到：原有出处、时间、排序和检索窗口；损坏向量不作为证据。
Token：不增加模型调用或检索结果条数。
缓存：仅读取原数据库，不修改历史、向量格式或提示词前缀。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import aiosqlite

from agent.memory.memory_utils import rank_embedding_blobs


class ConversationSearch:
    """复用宿主持有的数据库连接，不拥有写事务、连接关闭或迁移。"""

    def __init__(self, connection: Callable[[], Awaitable[aiosqlite.Connection]]) -> None:
        self._connection = connection

    async def search_vector(
        self,
        scope_type: str,
        scope_id: str,
        query_vec: list[float],
        *,
        limit: int = 5,
        skip_recent: int = 30,
        min_score: float = 0.25,
        scan_limit: int = 500,
    ) -> list[dict]:
        """向量语义搜索历史对话（跳过最近 skip_recent 条避免与上下文窗口重叠）。

        返回列表按相关度降序排列，每项包含 id / role / content / ts_ns / score。
        """
        db = await self._connection()

        # 计算时间截断点：第 skip_recent 条的 ts_ns
        cutoff_cursor = await db.execute(
            "SELECT ts_ns FROM conversation_messages "
            "WHERE scope_type=? AND scope_id=? ORDER BY ts_ns DESC LIMIT 1 OFFSET ?",
            (scope_type, scope_id, skip_recent),
        )
        cutoff_row = await cutoff_cursor.fetchone()
        if not cutoff_row:
            return []  # 历史消息不足 skip_recent 条，无需深度检索
        cutoff_ts: int = cutoff_row[0]

        # 加载截断点之前有 embedding 的消息（scan_limit 防止内存压力）
        rows_cursor = await db.execute(
            "SELECT id, role, content, ts_ns, embedding_blob FROM conversation_messages "
            "WHERE scope_type=? AND scope_id=? AND ts_ns<=? AND embedding_blob IS NOT NULL "
            "AND role IN ('user','assistant') ORDER BY ts_ns DESC LIMIT ?",
            (scope_type, scope_id, cutoff_ts, scan_limit),
        )
        rows = list(await rows_cursor.fetchall())
        if not rows:
            return []

        def score_rows() -> list[dict]:
            ranked: list[tuple[int, float]] = []
            for offset in range(0, len(rows), 128):
                hits = rank_embedding_blobs(query_vec, [row[4] for row in rows[offset:offset + 128]],
                                            limit=limit, min_score=min_score)
                ranked.extend((offset + i, score) for i, score in hits)
                ranked.sort(key=lambda hit: -hit[1])
                del ranked[limit:]
            return [{"id": rows[i][0], "role": rows[i][1], "content": rows[i][2],
                     "ts_ns": rows[i][3], "score": score} for i, score in ranked]

        return await asyncio.to_thread(score_rows)

    async def search_keyword(
        self,
        scope_type: str,
        scope_id: str,
        keywords: list[str],
        *,
        limit: int = 5,
        skip_recent: int = 30,
    ) -> list[dict]:
        """关键词搜索历史对话（无 embedding 时的 fallback）。"""
        if not keywords:
            return []
        db = await self._connection()

        cutoff_cursor = await db.execute(
            "SELECT ts_ns FROM conversation_messages "
            "WHERE scope_type=? AND scope_id=? ORDER BY ts_ns DESC LIMIT 1 OFFSET ?",
            (scope_type, scope_id, skip_recent),
        )
        cutoff_row = await cutoff_cursor.fetchone()
        if not cutoff_row:
            return []
        cutoff_ts: int = cutoff_row[0]

        like_clauses = " AND ".join("content LIKE ?" for _ in keywords)
        like_params = [f"%{kw}%" for kw in keywords]

        rows_cursor = await db.execute(
            f"SELECT id, role, content, ts_ns FROM conversation_messages "
            f"WHERE scope_type=? AND scope_id=? AND ts_ns<=? "
            f"AND role IN ('user','assistant') AND {like_clauses} "
            f"ORDER BY ts_ns DESC LIMIT ?",
            (scope_type, scope_id, cutoff_ts, *like_params, limit),
        )
        rows = await rows_cursor.fetchall()
        return [
            {"id": r[0], "role": r[1], "content": r[2], "ts_ns": r[3], "score": 0.0}
            for r in rows
        ]
