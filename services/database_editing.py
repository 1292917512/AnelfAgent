"""数据库行编辑编排：业务派生状态与正文同事务维护，提交后刷新运行时缓存。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import aiosqlite

from core.database import DatabaseCapability
from core.log import log
from services.database_catalog import require_database
from services.database_sqlite import SqliteDatabaseAdapter
from services.db_common import DatabaseError, looks_like_embedding_column


class DatabaseRowEditor:
    """通用行编辑的应用层；引擎连接与事务由 SQLite 适配器持有。"""

    def __init__(self, sqlite: SqliteDatabaseAdapter) -> None:
        self._sqlite = sqlite

    @staticmethod
    def _content_columns_changed(
        columns: List[Dict[str, Any]], changed: List[str]
    ) -> Optional[str]:
        """被改列中含内容列且表含 embedding 列时，返回 embedding 列名。

        对齐 MemoryStore.update(clear_embedding=True) 与
        SqliteBackend.update_conversation_message 的语义：
        内容变更后旧向量失效，置 NULL 等待后台 EmbeddingWorker 重建。
        """
        embedding_cols = [c["name"] for c in columns if looks_like_embedding_column(c["name"])]
        if not embedding_cols:
            return None
        content_like = {"content", "text", "description", "personality"}
        if any(n in content_like for n in changed):
            return embedding_cols[0]
        return None

    async def insert_row(self, db_id: str, table: str, values: Dict[str, Any]) -> Dict[str, Any]:
        require_database(db_id, DatabaseCapability.EDIT)
        conn = await self._sqlite.connection(db_id)
        columns = await self._sqlite.writable_columns(conn, table)
        self._sqlite.validate_columns(columns, list(values.keys()))
        if not values:
            raise DatabaseError("插入数据为空", status_code=400)
        names = list(values.keys())
        cols_sql = ", ".join(f'"{n}"' for n in names)
        placeholders = ", ".join("?" for _ in names)
        async with self._row_write(db_id, table, conn):
            cursor = await conn.execute(
                f'INSERT INTO "{table}" ({cols_sql}) VALUES ({placeholders})',
                [values[n] for n in names],
            )
        log(f"数据库管理: 插入行 {db_id}/{table} rowid={cursor.lastrowid}", "DEBUG", tag="数据库")
        return {"rowid": cursor.lastrowid}

    async def update_row(self, db_id: str, table: str, rowid: int, values: Dict[str, Any]) -> None:
        require_database(db_id, DatabaseCapability.EDIT)
        conn = await self._sqlite.connection(db_id)
        columns = await self._sqlite.writable_columns(conn, table)
        self._sqlite.validate_columns(columns, list(values.keys()))
        if not values:
            raise DatabaseError("更新数据为空", status_code=400)

        assignments = dict(values)
        # 内容列变更 → 失效对应 embedding（等后台重建，避免语义检索用到过期向量）
        stale_embedding_col = self._content_columns_changed(columns, list(values.keys()))
        if stale_embedding_col and stale_embedding_col not in assignments:
            assignments[stale_embedding_col] = None

        set_sql = ", ".join(f'"{n}" = ?' for n in assignments)
        memory_table = db_id == "memory" and table == "memories"
        vector_invalidated = (
            memory_table and "content" in values
            and stale_embedding_col == "embedding_blob"
            and assignments.get("embedding_blob") is None
        )
        async with self._row_write(db_id, table, conn):
            cursor = await conn.execute(
                f'UPDATE "{table}" SET {set_sql} WHERE rowid = ?',
                (*assignments.values(), rowid),
            )
            if cursor.rowcount == 0:
                raise DatabaseError(f"行不存在: rowid={rowid}", status_code=404)
            if memory_table:
                from agent.memory.store.management import maintain_memory_update
                await maintain_memory_update(conn, rowid, values, columns, vector_invalidated=vector_invalidated)
        if vector_invalidated:
            from agent.memory.embedding import wake_embedding_worker
            wake_embedding_worker()
        log(f"数据库管理: 更新行 {db_id}/{table} rowid={rowid} 列={list(values.keys())}", "DEBUG", tag="数据库")

    async def delete_row(self, db_id: str, table: str, rowid: int) -> None:
        require_database(db_id, DatabaseCapability.EDIT)
        conn = await self._sqlite.connection(db_id)
        await self._sqlite.writable_columns(conn, table)
        async with self._row_write(db_id, table, conn):
            cursor = await conn.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (rowid,))
        if cursor.rowcount == 0:
            raise DatabaseError(f"行不存在: rowid={rowid}", status_code=404)
        log(f"数据库管理: 删除行 {db_id}/{table} rowid={rowid}", "DEBUG", tag="数据库")

    @asynccontextmanager
    async def _row_write(self, db_id: str, table: str, conn: aiosqlite.Connection) -> AsyncIterator[None]:
        """提交行编辑；失败或取消时回滚，并使运行时会话窗口重新取数。"""
        try:
            async with self._sqlite.transaction(db_id, conn):
                yield
        finally:
            # commit 取消时可能已落盘；窗口必须重新取数而非猜测事务结果。
            self._invalidate_conversation_window(db_id, table)

    def _invalidate_conversation_window(self, db_id: str, table: str) -> None:
        """数据库编辑与运行时指向同一主库时，失效其会话窗口。"""
        if db_id != "agent" or table != "conversation_messages":
            return
        from services._runtime import get_runtime

        runtime = get_runtime()
        if runtime is not None:
            sqlite = runtime.data_center.sqlite
            if Path(sqlite.db_path).resolve() == Path(self._sqlite.path(db_id)).resolve():
                sqlite.invalidate_conversation_windows()
