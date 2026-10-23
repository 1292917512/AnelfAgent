"""SQLite 管理适配器：连接、表结构、只读查询、事务与维护。

安全约定：
- 库 ID / 表名 / 列名一律先经 sqlite_master / PRAGMA 白名单校验；
  标识符双引号包裹，值全部参数化
- 视图、FTS5/vec0 虚表及影子表只读；影子表默认不出现在表清单
- run_query 只允许 SELECT/WITH/EXPLAIN/PRAGMA 单语句，自动补 LIMIT
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import aiosqlite

from core.database import DatabaseCapability
from core.log import log
from core.sqlite_utils import online_sqlite_backup, required_row
from services.database_catalog import require_database
from services.db_common import (
    CELL_TEXT_MAX,
    SHADOW_PATTERNS,
    DatabaseError,
    serialize_value,
)

_QUERY_MAX_ROWS = 500
_QUERY_TIMEOUT_SECONDS = 10.0

# 只读查询允许的首关键字
_QUERY_ALLOWED_KEYWORDS = ("select", "with", "explain", "pragma")

# 维护操作白名单与阈值
_OPTIMIZE_ACTIONS = ("checkpoint", "vacuum", "analyze")
_WAL_WARN_BYTES = 32 * 1024 * 1024  # WAL 超过 32MB 建议 checkpoint
_FRAGMENT_WARN_RATIO = 0.2  # 空闲页占比超过 20% 建议 VACUUM
_FRAGMENT_MIN_PAGES = 100  # 小库不报碎片化（无意义）



def _full_value(value: Any, column: str) -> Any:
    """单行详情用：不截断的序列化。"""
    if isinstance(value, str) and len(value) > CELL_TEXT_MAX:
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return {"__type__": "json", "value": json.loads(stripped), "raw": value}
            except (ValueError, TypeError):
                log("_full_value 异常已忽略", "DEBUG")
        return {"__type__": "text", "text": value}
    return serialize_value(value, column)


class SqliteDatabaseAdapter:
    """SQLite 浏览、连接和维护；不含业务表语义或其他引擎分派。"""

    def __init__(self) -> None:
        self._connections: Dict[str, aiosqlite.Connection] = {}
        self._conn_locks: Dict[str, asyncio.Lock] = {}

    def path(self, db_id: str) -> str:
        entry = require_database(db_id, DatabaseCapability.BROWSE)
        if entry.component.engine != "sqlite":
            raise DatabaseError(f"不支持的 SQLite 管理引擎: {entry.component.engine}", status_code=403)
        path = entry.path
        if not os.path.isfile(path):
            raise DatabaseError(f"数据库文件不存在: {path}", status_code=404)
        return path

    async def connection(self, db_id: str) -> aiosqlite.Connection:
        require_database(db_id, DatabaseCapability.EDIT)
        lock = self._conn_locks.setdefault(db_id, asyncio.Lock())
        async with lock:
            conn = self._connections.get(db_id)
            if conn is not None:
                try:
                    await conn.execute("SELECT 1")
                    return conn
                except Exception:
                    try:
                        await conn.close()
                    except Exception as exc:
                        log(f"数据库管理失效连接关闭失败 {db_id}: {exc}", "ERROR", tag="数据库")
                        raise
                    self._connections.pop(db_id, None)

            path = self.path(db_id)
            conn = await self._open_conn(path)
            self._connections[db_id] = conn
            return conn

    @asynccontextmanager
    async def read_connection(self, db_id: str) -> AsyncIterator[aiosqlite.Connection]:
        """受管组件使用短只读连接，避免发布/恢复后仍持有旧数据库句柄。"""
        entry = require_database(db_id, DatabaseCapability.BROWSE)
        if DatabaseCapability.EDIT in entry.component.capabilities:
            yield await self.connection(db_id)
            return
        conn = await self._open_conn(self.path(db_id), readonly=True)
        try:
            yield conn
        finally:
            await conn.close()

    @staticmethod
    def _readonly(db_id: str) -> bool:
        entry = require_database(db_id, DatabaseCapability.BROWSE)
        return DatabaseCapability.EDIT not in entry.component.capabilities

    @staticmethod
    async def _open_conn(path: str, *, readonly: bool = False) -> aiosqlite.Connection:
        target = Path(path).resolve().as_uri() + "?mode=ro" if readonly else path
        conn = await aiosqlite.connect(target, uri=readonly)
        try:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout=5000")
            try:
                import sqlite_vec

                await conn.enable_load_extension(True)
                try:
                    await conn.load_extension(sqlite_vec.loadable_path())
                finally:
                    await conn.enable_load_extension(False)
            except Exception as exc:
                log(f"数据库管理: sqlite-vec 加载失败（vec 虚表不可查）: {exc}", "DEBUG", tag="数据库")
            if readonly:
                await conn.execute("PRAGMA query_only=ON")
            return conn
        except BaseException:
            await conn.close()
            raise

    async def close_all(self) -> None:
        failed: list[str] = []
        for db_id, conn in list(self._connections.items()):
            try:
                await conn.close()
            except Exception as exc:
                failed.append(db_id)
                log(f"数据库管理连接关闭失败 {db_id}: {exc}", "ERROR", tag="数据库")
            else:
                self._connections.pop(db_id, None)
                self._conn_locks.pop(db_id, None)
        if failed:
            raise RuntimeError(f"数据库管理连接尚未关闭: {', '.join(failed)}")

    @staticmethod
    def _is_shadow_table(name: str) -> bool:
        return any(pat in name for pat in SHADOW_PATTERNS)

    async def _table_meta(self, conn: aiosqlite.Connection, table: str) -> Dict[str, Any]:
        """校验表存在并返回 {type, readonly, ddl}。"""
        cursor = await conn.execute(
            "SELECT name, type, sql FROM sqlite_master WHERE name = ? AND type IN ('table', 'view')",
            (table,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise DatabaseError(f"表不存在: {table}", status_code=404)
        tbl_type = row["type"]
        ddl = row["sql"] or ""
        readonly = tbl_type == "view"
        # FTS5 / vec0 虚表（CREATE VIRTUAL TABLE）不允许直接写
        if "VIRTUAL TABLE" in ddl.upper():
            readonly = True
        if self._is_shadow_table(table):
            readonly = True
        return {"type": tbl_type, "readonly": readonly, "ddl": ddl}

    async def _table_columns(self, conn: aiosqlite.Connection, table: str) -> List[Dict[str, Any]]:
        cursor = await conn.execute(f'PRAGMA table_info("{table}")')
        rows = await cursor.fetchall()
        return [
            {
                "cid": r["cid"],
                "name": r["name"],
                "type": (r["type"] or "").upper(),
                "notnull": bool(r["notnull"]),
                "default": r["dflt_value"],
                "pk": bool(r["pk"]),
            }
            for r in rows
        ]

    @staticmethod
    def validate_columns(columns: List[Dict[str, Any]], names: List[str]) -> None:
        valid = {c["name"] for c in columns}
        bad = [n for n in names if n not in valid]
        if bad:
            raise DatabaseError(f"未知列: {', '.join(bad)}", status_code=400)

    async def table_count(self, db_id: str) -> int:
        """轻量目录观测，只读取 SQLite 元数据。"""
        async with self.read_connection(db_id) as conn:
            row = await (await conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
            )).fetchone()
            return int(required_row(row)[0])

    async def list_tables(self, db_id: str, include_shadow: bool = False) -> List[Dict[str, Any]]:
        readonly = self._readonly(db_id)
        async with self.read_connection(db_id) as conn:
            cursor = await conn.execute(
                "SELECT name, type, sql FROM sqlite_master "
                "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
            tables: List[Dict[str, Any]] = []
            for row in await cursor.fetchall():
                name = row["name"]
                shadow = self._is_shadow_table(name)
                if shadow and not include_shadow:
                    continue
                ddl = row["sql"] or ""
                virtual = "VIRTUAL TABLE" in ddl.upper()
                try:
                    count_cursor = await conn.execute(f'SELECT COUNT(*) AS c FROM "{name}"')
                    count_row = await count_cursor.fetchone()
                    row_count = count_row["c"] if count_row else 0
                except Exception:
                    row_count = -1
                columns = await self._table_columns(conn, name)
                tables.append(
                    {
                        "name": name,
                        "type": row["type"],
                        "virtual": virtual,
                        "shadow": shadow,
                        "readonly": readonly or row["type"] == "view" or virtual or shadow,
                        "row_count": row_count,
                        "column_count": len(columns),
                    }
                )
            return tables

    async def table_schema(self, db_id: str, table: str) -> Dict[str, Any]:
        async with self.read_connection(db_id) as conn:
            meta = await self._table_meta(conn, table)
            columns = await self._table_columns(conn, table)
            cursor = await conn.execute(f'PRAGMA index_list("{table}")')
            indexes = []
            for idx in await cursor.fetchall():
                col_cursor = await conn.execute(f'PRAGMA index_info("{idx["name"]}")')
                idx_cols = [r["name"] for r in await col_cursor.fetchall()]
                indexes.append(
                    {"name": idx["name"], "unique": bool(idx["unique"]), "columns": idx_cols}
                )
            return {
                "table": table,
                "type": meta["type"],
                "readonly": self._readonly(db_id) or meta["readonly"],
                "ddl": meta["ddl"],
                "columns": columns,
                "indexes": indexes,
            }

    async def browse_rows(
        self,
        db_id: str,
        table: str,
        *,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[str] = None,
        order: str = "asc",
        filter_col: Optional[str] = None,
        filter_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        async with self.read_connection(db_id) as conn:
            meta = await self._table_meta(conn, table)
            columns = await self._table_columns(conn, table)
            col_names = [c["name"] for c in columns]

            where_sql = ""
            params: List[Any] = []
            if filter_col and filter_text:
                self.validate_columns(columns, [filter_col])
                where_sql = f'WHERE CAST("{filter_col}" AS TEXT) LIKE ?'
                params.append(f"%{filter_text}%")

            order_sql = ""
            if sort:
                self.validate_columns(columns, [sort])
                direction = "DESC" if order.lower() == "desc" else "ASC"
                order_sql = f'ORDER BY "{sort}" {direction}'

            count_cursor = await conn.execute(
                f'SELECT COUNT(*) AS c FROM "{table}" {where_sql}', params,
            )
            total = required_row(await count_cursor.fetchone())["c"]

            page = max(1, page)
            page_size = max(1, min(page_size, 200))
            offset = (page - 1) * page_size
            cursor = await conn.execute(
                f'SELECT rowid AS __rowid__, * FROM "{table}" {where_sql} {order_sql} LIMIT ? OFFSET ?',
                (*params, page_size, offset),
            )
            rows: List[Dict[str, Any]] = []
            for r in await cursor.fetchall():
                rows.append(
                    {
                        "__rowid__": r["__rowid__"],
                        "values": {name: serialize_value(r[name], name) for name in col_names},
                    }
                )
            pages = (total + page_size - 1) // page_size if total else 0
            return {
                "items": rows,
                "columns": columns,
                "total": total,
                "page": page,
                "page_size": page_size,
                "pages": pages,
                "readonly": self._readonly(db_id) or meta["readonly"],
            }

    async def get_row(self, db_id: str, table: str, rowid: int) -> Dict[str, Any]:
        async with self.read_connection(db_id) as conn:
            await self._table_meta(conn, table)
            columns = await self._table_columns(conn, table)
            cursor = await conn.execute(
                f'SELECT rowid AS __rowid__, * FROM "{table}" WHERE rowid = ?', (rowid,),
            )
            r = await cursor.fetchone()
            if r is None:
                raise DatabaseError(f"行不存在: rowid={rowid}", status_code=404)
            return {
                "__rowid__": r["__rowid__"],
                "values": {c["name"]: _full_value(r[c["name"]], c["name"]) for c in columns},
            }

    async def writable_columns(self, conn: aiosqlite.Connection, table: str) -> List[Dict[str, Any]]:
        meta = await self._table_meta(conn, table)
        if meta["readonly"]:
            raise DatabaseError(f"表 {table} 为只读（视图/虚表/影子表），不支持编辑", status_code=403)
        return await self._table_columns(conn, table)

    @staticmethod
    def validate_readonly_sql(sql: str) -> str:
        text = sql.strip().rstrip(";").strip()
        if not text:
            raise DatabaseError("SQL 为空", status_code=400)
        if ";" in text:
            raise DatabaseError("只允许单条语句", status_code=400)
        first = re.match(r"^\s*(?:--[^\n]*\n\s*)*(\w+)", text)
        keyword = (first.group(1).lower() if first else "")
        if keyword not in _QUERY_ALLOWED_KEYWORDS:
            raise DatabaseError(
                f"只允许只读语句（{', '.join(k.upper() for k in _QUERY_ALLOWED_KEYWORDS)}）",
                status_code=403,
            )
        # PRAGMA 带等号是写操作（如 PRAGMA journal_mode=DELETE）
        if keyword == "pragma" and "=" in text:
            raise DatabaseError("只允许只读 PRAGMA（不允许赋值）", status_code=403)
        # 无 LIMIT 的 SELECT 自动补上限
        if keyword in ("select", "with") and not re.search(r"\blimit\b", text, re.IGNORECASE):
            text = f"{text} LIMIT {_QUERY_MAX_ROWS}"
        return text

    async def run_query(self, db_id: str, sql: str) -> Dict[str, Any]:
        require_database(db_id, DatabaseCapability.QUERY)
        safe_sql = self.validate_readonly_sql(sql)
        # 查询使用独立只读连接，不能通过 WITH 写语句或 PRAGMA 括号语法改动持久化数据。
        conn = await self._open_conn(self.path(db_id), readonly=True)
        started = time.time()
        deadline = time.monotonic() + _QUERY_TIMEOUT_SECONDS
        await conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)

        async def _do() -> Tuple[List[str], List[Dict[str, Any]]]:
            cursor = await conn.execute(safe_sql)
            rows = await cursor.fetchmany(_QUERY_MAX_ROWS)
            col_names = [d[0] for d in cursor.description] if cursor.description else []
            return col_names, [
                {name: serialize_value(r[name], name) for name in col_names} for r in rows
            ]

        task = asyncio.create_task(_do())
        try:
            columns, rows = await asyncio.wait_for(asyncio.shield(task), timeout=_QUERY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise DatabaseError(f"查询超时（{_QUERY_TIMEOUT_SECONDS:.0f}s）", status_code=400) from None
        except DatabaseError:
            raise
        except Exception as exc:
            if time.monotonic() >= deadline:
                raise DatabaseError(f"查询超时（{_QUERY_TIMEOUT_SECONDS:.0f}s）", status_code=400) from exc
            raise DatabaseError(f"查询失败: {exc}", status_code=400) from exc
        finally:
            if not task.done():
                await conn.interrupt()
                await asyncio.gather(task, return_exceptions=True)
            await conn.close()
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "elapsed_ms": round((time.time() - started) * 1000, 1),
            "truncated": len(rows) >= _QUERY_MAX_ROWS,
        }

    async def database_health(self, db_id: str) -> Dict[str, Any]:
        """库健康概览：体积 / WAL / 碎片化 / 表规模 / 维护建议。"""
        require_database(db_id, DatabaseCapability.HEALTH)
        conn = await self.connection(db_id)
        path = self.path(db_id)

        size_bytes = os.path.getsize(path)
        wal_path = f"{path}-wal"
        wal_bytes = os.path.getsize(wal_path) if os.path.isfile(wal_path) else 0

        page_count = required_row(await (await conn.execute("PRAGMA page_count")).fetchone())[0]
        freelist_count = required_row(await (await conn.execute("PRAGMA freelist_count")).fetchone())[0]
        fragmentation = round(freelist_count / page_count, 4) if page_count else 0.0

        tables = await self.list_tables(db_id)
        top_tables = sorted(
            (t for t in tables if t["row_count"] >= 0),
            key=lambda t: t["row_count"],
            reverse=True,
        )[:5]

        suggestions: List[Dict[str, Any]] = []
        if wal_bytes > _WAL_WARN_BYTES:
            suggestions.append({
                "id": "wal_oversize", "level": "warn", "action": "checkpoint",
                "detail": {"wal_bytes": wal_bytes},
            })
        if page_count >= _FRAGMENT_MIN_PAGES and fragmentation > _FRAGMENT_WARN_RATIO:
            suggestions.append({
                "id": "fragmented", "level": "warn", "action": "vacuum",
                "detail": {"fragmentation": fragmentation},
            })
        suggestions.append({"id": "analyze", "level": "info", "action": "analyze", "detail": {}})

        return {
            "id": db_id,
            "size_bytes": size_bytes,
            "wal_bytes": wal_bytes,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "fragmentation": fragmentation,
            "top_tables": [
                {"name": t["name"], "row_count": t["row_count"]} for t in top_tables
            ],
            "suggestions": suggestions,
        }

    async def backup_database(self, db_id: str) -> Dict[str, Any]:
        """在线热备份（SQLite Backup API，运行中安全）到 <data_dir>/backups/。"""
        require_database(db_id, DatabaseCapability.BACKUP)
        path = self.path(db_id)
        from core.path import ConfigPaths, project_root
        backup_dir = Path(project_root()) / ConfigPaths.MEMORY_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{db_id}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
        dest = backup_dir / filename

        started = time.time()
        await online_sqlite_backup(Path(path), dest)

        size = dest.stat().st_size
        log(f"数据库管理: 备份 {db_id} -> {dest} ({size} 字节)", tag="数据库")
        return {
            "path": str(dest),
            "filename": filename,
            "size_bytes": size,
            "elapsed_ms": round((time.time() - started) * 1000, 1),
        }

    async def optimize_database(self, db_id: str, actions: List[str]) -> Dict[str, Any]:
        """执行维护操作（白名单：checkpoint / vacuum / analyze）。"""
        require_database(db_id, DatabaseCapability.OPTIMIZE)
        bad = [a for a in actions if a not in _OPTIMIZE_ACTIONS]
        if bad:
            raise DatabaseError(f"未知维护操作: {', '.join(bad)}", status_code=400)
        if not actions:
            raise DatabaseError("维护操作为空", status_code=400)

        conn = await self.connection(db_id)
        results: List[Dict[str, Any]] = []
        async with self.transaction(db_id, conn):
            for action in actions:
                started = time.time()
                entry: Dict[str, Any] = {"action": action}
                if action == "checkpoint":
                    cursor = await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    row = await cursor.fetchone()
                    if row:
                        entry["detail"] = {"busy": row[0], "wal_frames": row[1], "checkpointed": row[2]}
                elif action == "vacuum":
                    # VACUUM 不能在事务内执行，先提交管理连接上的挂起事务
                    await conn.commit()
                    await conn.execute("VACUUM")
                elif action == "analyze":
                    await conn.execute("ANALYZE")
                    await conn.commit()
                entry["elapsed_ms"] = round((time.time() - started) * 1000, 1)
                results.append(entry)
                log(f"数据库管理: 优化 {db_id} [{action}] 完成 ({entry['elapsed_ms']}ms)", tag="数据库")
        return {"actions": results}

    @asynccontextmanager
    async def transaction(self, db_id: str, conn: aiosqlite.Connection) -> AsyncIterator[None]:
        """串行提交管理写事务；异常或取消时回滚。"""
        async with self._conn_locks.setdefault(db_id, asyncio.Lock()):
            try:
                yield
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
