"""工具错误追踪：tool_errors 表的记录、查询、统计与解决标记。

连接与事务由 MemoryConnectionManager 提供，本模块不自建连接。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from core.log import log

from .connection import MemoryConnectionManager


class ToolErrorTracker:
    """工具执行错误的持久化追踪。"""

    def __init__(self, conn: MemoryConnectionManager) -> None:
        self._conn = conn

    async def record(
        self,
        tool_name: str,
        error_type: str,
        error_msg: str,
        args_json: str = "{}",
        context: str = "",
    ) -> Optional[int]:
        """记录工具执行错误，返回记录 ID。"""
        try:
            db = await self._conn.get_db()
            async with self._conn.tx(db):
                cursor = await db.execute(
                    "INSERT INTO tool_errors (tool_name, error_type, error_msg, args_json, context, ts_ns) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (tool_name, error_type, error_msg[:500], args_json[:500], context[:200],
                     int(time.time() * 1e9)),
                )
            return cursor.lastrowid
        except Exception as e:
            log(f"记录工具错误失败: {e}", "DEBUG")
            return None

    async def get_errors(
        self,
        tool_name: str = "",
        limit: int = 20,
        unresolved_only: bool = False,
    ) -> list[Dict[str, Any]]:
        """查询工具错误历史。"""
        db = await self._conn.get_db()
        conditions: list[str] = []
        params: list[Any] = []
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if unresolved_only:
            conditions.append("resolved = 0")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        cursor = await db.execute(
            f"SELECT id, tool_name, error_type, error_msg, args_json, context, resolved, ts_ns "
            f"FROM tool_errors{where} ORDER BY ts_ns DESC LIMIT ?",
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r["id"],
                "tool_name": r["tool_name"],
                "error_type": r["error_type"],
                "error_msg": r["error_msg"],
                "args_preview": r["args_json"][:100],
                "context": r["context"],
                "resolved": bool(r["resolved"]),
                "time": r["ts_ns"] / 1e9,
            }
            for r in rows
        ]

    async def get_stats(self) -> list[Dict[str, Any]]:
        """按工具名统计错误次数。"""
        db = await self._conn.get_db()
        cursor = await db.execute(
            "SELECT tool_name, COUNT(*) as count, "
            "SUM(CASE WHEN resolved = 0 THEN 1 ELSE 0 END) as unresolved "
            "FROM tool_errors GROUP BY tool_name ORDER BY count DESC"
        )
        rows = await cursor.fetchall()
        return [
            {"tool_name": r["tool_name"], "total": r["count"], "unresolved": r["unresolved"]}
            for r in rows
        ]

    async def resolve(self, error_id: int) -> bool:
        """标记工具错误为已解决。"""
        db = await self._conn.get_db()
        async with self._conn.tx(db):
            cursor = await db.execute(
                "UPDATE tool_errors SET resolved = 1 WHERE id = ?", (error_id,)
            )
        return (cursor.rowcount or 0) > 0
