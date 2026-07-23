"""数据库管理服务 — WebUI「数据管理」页的后端核心。

管理 AnelfAgent 的全部 SQLite 数据库（独立于 Agent 运行时的自有连接，
Agent 挂掉时也可用于排查/维护）：

- ``agent``    — 主库（会话消息 / 实体画像 / 别名 / 待办）
- ``memory``   — 记忆库（长期记忆 / 文档索引 / FTS5 / vec0 / Cognee outbox）
- ``stickers`` — 表情包库
- ``cognee``   — Cognee 关系库（lbug/lance 非 SQLite，不纳入）

安全约定：
- 库 ID / 表名 / 列名一律先经 sqlite_master / PRAGMA 白名单校验；
  标识符双引号包裹，值全部参数化
- 视图、FTS5/vec0 虚表及影子表只读；影子表默认不出现在表清单
- run_query 只允许 SELECT/WITH/EXPLAIN/PRAGMA 单语句，自动补 LIMIT
"""

from __future__ import annotations

import array
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from core.log import log

# ----------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------

_QUERY_MAX_ROWS = 500
_QUERY_TIMEOUT_SECONDS = 10.0
_CELL_TEXT_MAX = 500  # 浏览时单元格文本截断长度（全文走单行详情接口）

# FTS5 / vec0 影子表（内部维护，默认不出现在表清单）
_SHADOW_PATTERNS = (
    "_fts_data",
    "_fts_idx",
    "_fts_docsize",
    "_fts_config",
    "_fts_content",
    "_vec_chunks",
    "_vec_rowids",
    "_vec_vector_chunks",
    "_vec_info",
)

# 只读查询允许的首关键字
_QUERY_ALLOWED_KEYWORDS = ("select", "with", "explain", "pragma")


class DatabaseError(RuntimeError):
    """数据库管理操作错误（router 转成 HTTPException）。"""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ----------------------------------------------------------------------
# 库注册表
# ----------------------------------------------------------------------

def _main_db_path() -> str:
    """主库路径（与 agent/storage/sqlite_backend.py 的 default_sqlite_path 同源）。"""
    env_path = os.getenv("ANELF_BOT_SQLITE_PATH")
    if env_path and env_path.strip():
        return env_path.strip()
    from core.path import ConfigPaths, project_root
    return str(Path(project_root()) / ConfigPaths.SQLITE_DB)


def _cognee_db_path() -> str:
    from core.path import ConfigPaths, project_root
    return str(Path(project_root()) / ConfigPaths.COGNEE_DATA_DIR / "system" / "databases" / "cognee_db")


def _database_registry() -> Dict[str, Dict[str, str]]:
    main = _main_db_path()
    stem = main[: -len(".sqlite3")] if main.endswith(".sqlite3") else os.path.splitext(main)[0]
    return {
        "agent": {
            "name": "会话主库",
            "path": main,
            "description": "会话消息 / 实体画像 / 身份别名 / 待办任务",
        },
        "memory": {
            "name": "长期记忆库",
            "path": f"{stem}_memory.sqlite3",
            "description": "长期记忆 / 归档 / 文档索引 / 向量 / Cognee 同步队列",
        },
        "stickers": {
            "name": "表情包库",
            "path": f"{stem}_stickers.sqlite3",
            "description": "表情包 / 图片感知索引",
        },
        "cognee": {
            "name": "Cognee 关系库",
            "path": _cognee_db_path(),
            "description": "Cognee 知识图谱投影的关系数据（lbug/lance 引擎文件不支持）",
        },
    }


# ----------------------------------------------------------------------
# 值序列化（智能展示：blob / 向量 / JSON / 时间戳 / 长文本）
# ----------------------------------------------------------------------

def _looks_like_embedding_column(column: str) -> bool:
    name = column.lower()
    return "embedding" in name or name.endswith("_vec") or "vector" in name


def _serialize_value(value: Any, column: str) -> Any:
    """把 SQLite 值转成 JSON 可序列化的智能结构。"""
    if value is None:
        return None
    if isinstance(value, bytes):
        info: Dict[str, Any] = {"__type__": "blob", "bytes": len(value)}
        # float32 小端向量（embedding_blob / embedding 列）
        if len(value) >= 4 and len(value) % 4 == 0 and _looks_like_embedding_column(column):
            try:
                arr = array.array("f")
                arr.frombytes(value[: 4 * 4])  # 预览前 4 维
                info["__type__"] = "vec"
                info["dims"] = len(value) // 4
                info["preview"] = [round(float(x), 4) for x in arr]
            except Exception:
                pass
        return info
    if isinstance(value, (int, float)):
        # *_ns 纳秒时间戳列 → 附可读时间
        if column.endswith("_ns") and isinstance(value, int) and value > 10**15:
            return {
                "__type__": "ts",
                "value": value,
                "text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value / 1e9)),
            }
        return value
    text = str(value)
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
            out: Dict[str, Any] = {
                "__type__": "json",
                "value": parsed,
                "raw": text if len(text) <= _CELL_TEXT_MAX else text[:_CELL_TEXT_MAX],
            }
            if len(text) > _CELL_TEXT_MAX:
                out["truncated"] = True
            return out
        except (ValueError, TypeError):
            pass
    if len(text) > _CELL_TEXT_MAX:
        return {"__type__": "text", "text": text[:_CELL_TEXT_MAX], "truncated": True}
    return text


def _full_value(value: Any, column: str) -> Any:
    """单行详情用：不截断的序列化。"""
    if isinstance(value, str) and len(value) > _CELL_TEXT_MAX:
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return {"__type__": "json", "value": json.loads(stripped), "raw": value}
            except (ValueError, TypeError):
                pass
        return {"__type__": "text", "text": value}
    return _serialize_value(value, column)


# ----------------------------------------------------------------------
# DatabaseService
# ----------------------------------------------------------------------

class DatabaseService:
    """SQLite 数据库管理（无状态，模块级单例使用）。"""

    def __init__(self) -> None:
        self._connections: Dict[str, aiosqlite.Connection] = {}
        self._conn_locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def _db_path(self, db_id: str) -> str:
        registry = _database_registry()
        if db_id not in registry:
            raise DatabaseError(f"未知数据库: {db_id}", status_code=404)
        path = registry[db_id]["path"]
        if not os.path.isfile(path):
            raise DatabaseError(f"数据库文件不存在: {path}", status_code=404)
        return path

    async def _get_conn(self, db_id: str) -> aiosqlite.Connection:
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
                    except Exception:
                        pass
                    self._connections.pop(db_id, None)

            path = self._db_path(db_id)
            conn = await aiosqlite.connect(path)
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout=5000;")
            # sqlite-vec 为连接级扩展，加载后 vec0 虚表才可查询
            try:
                import sqlite_vec

                await conn.enable_load_extension(True)
                try:
                    await conn.load_extension(sqlite_vec.loadable_path())
                finally:
                    await conn.enable_load_extension(False)
            except Exception as exc:
                log(f"数据库管理: sqlite-vec 加载失败（vec 虚表不可查）: {exc}", "DEBUG", tag="数据库")
            self._connections[db_id] = conn
            return conn

    async def close_all(self) -> None:
        for conn in self._connections.values():
            try:
                await conn.close()
            except Exception:
                pass
        self._connections.clear()

    # ------------------------------------------------------------------
    # 白名单校验
    # ------------------------------------------------------------------

    @staticmethod
    def _is_shadow_table(name: str) -> bool:
        return any(pat in name for pat in _SHADOW_PATTERNS)

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
    def _validate_columns(columns: List[Dict[str, Any]], names: List[str]) -> None:
        valid = {c["name"] for c in columns}
        bad = [n for n in names if n not in valid]
        if bad:
            raise DatabaseError(f"未知列: {', '.join(bad)}", status_code=400)

    # ------------------------------------------------------------------
    # 库 / 表清单
    # ------------------------------------------------------------------

    async def list_databases(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for db_id, info in _database_registry().items():
            path = info["path"]
            exists = os.path.isfile(path)
            entry: Dict[str, Any] = {
                "id": db_id,
                "name": info["name"],
                "description": info["description"],
                "path": path,
                "exists": exists,
                "size_bytes": os.path.getsize(path) if exists else 0,
                "table_count": 0,
            }
            if exists:
                try:
                    conn = await self._get_conn(db_id)
                    cursor = await conn.execute(
                        "SELECT COUNT(*) AS c FROM sqlite_master "
                        "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
                    )
                    row = await cursor.fetchone()
                    entry["table_count"] = row["c"] if row else 0
                except DatabaseError:
                    pass
                except Exception as exc:
                    entry["error"] = str(exc)
            result.append(entry)
        return result

    async def list_tables(self, db_id: str, include_shadow: bool = False) -> List[Dict[str, Any]]:
        conn = await self._get_conn(db_id)
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
                    "readonly": row["type"] == "view" or virtual or shadow,
                    "row_count": row_count,
                    "column_count": len(columns),
                }
            )
        return tables

    async def table_schema(self, db_id: str, table: str) -> Dict[str, Any]:
        conn = await self._get_conn(db_id)
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
            "readonly": meta["readonly"],
            "ddl": meta["ddl"],
            "columns": columns,
            "indexes": indexes,
        }

    # ------------------------------------------------------------------
    # 行浏览
    # ------------------------------------------------------------------

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
        conn = await self._get_conn(db_id)
        meta = await self._table_meta(conn, table)
        columns = await self._table_columns(conn, table)
        col_names = [c["name"] for c in columns]

        where_sql = ""
        params: List[Any] = []
        if filter_col and filter_text:
            self._validate_columns(columns, [filter_col])
            where_sql = f'WHERE CAST("{filter_col}" AS TEXT) LIKE ?'
            params.append(f"%{filter_text}%")

        order_sql = ""
        if sort:
            self._validate_columns(columns, [sort])
            direction = "DESC" if order.lower() == "desc" else "ASC"
            order_sql = f'ORDER BY "{sort}" {direction}'

        count_cursor = await conn.execute(
            f'SELECT COUNT(*) AS c FROM "{table}" {where_sql}', params,
        )
        total = (await count_cursor.fetchone())["c"]

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
                    "values": {name: _serialize_value(r[name], name) for name in col_names},
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
            "readonly": meta["readonly"],
        }

    async def get_row(self, db_id: str, table: str, rowid: int) -> Dict[str, Any]:
        conn = await self._get_conn(db_id)
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

    # ------------------------------------------------------------------
    # 行编辑（写）
    # ------------------------------------------------------------------

    async def _require_writable(self, conn: aiosqlite.Connection, table: str) -> List[Dict[str, Any]]:
        meta = await self._table_meta(conn, table)
        if meta["readonly"]:
            raise DatabaseError(f"表 {table} 为只读（视图/虚表/影子表），不支持编辑", status_code=403)
        return await self._table_columns(conn, table)

    @staticmethod
    def _content_columns_changed(
        columns: List[Dict[str, Any]], changed: List[str]
    ) -> Optional[str]:
        """被改列中含内容列且表含 embedding 列时，返回 embedding 列名。

        对齐 MemoryStore.update(clear_embedding=True) 与
        SqliteBackend.update_conversation_message 的语义：
        内容变更后旧向量失效，置 NULL 等待后台 EmbeddingWorker 重建。
        """
        embedding_cols = [c["name"] for c in columns if _looks_like_embedding_column(c["name"])]
        if not embedding_cols:
            return None
        content_like = {"content", "text", "description", "personality"}
        if any(n in content_like for n in changed):
            return embedding_cols[0]
        return None

    async def insert_row(self, db_id: str, table: str, values: Dict[str, Any]) -> Dict[str, Any]:
        conn = await self._get_conn(db_id)
        columns = await self._require_writable(conn, table)
        self._validate_columns(columns, list(values.keys()))
        if not values:
            raise DatabaseError("插入数据为空", status_code=400)
        names = list(values.keys())
        cols_sql = ", ".join(f'"{n}"' for n in names)
        placeholders = ", ".join("?" for _ in names)
        cursor = await conn.execute(
            f'INSERT INTO "{table}" ({cols_sql}) VALUES ({placeholders})',
            [values[n] for n in names],
        )
        await conn.commit()
        log(f"数据库管理: 插入行 {db_id}/{table} rowid={cursor.lastrowid}", "DEBUG", tag="数据库")
        return {"rowid": cursor.lastrowid}

    async def update_row(self, db_id: str, table: str, rowid: int, values: Dict[str, Any]) -> None:
        conn = await self._get_conn(db_id)
        columns = await self._require_writable(conn, table)
        self._validate_columns(columns, list(values.keys()))
        if not values:
            raise DatabaseError("更新数据为空", status_code=400)

        assignments = dict(values)
        # 内容列变更 → 失效对应 embedding（等后台重建，避免语义检索用到过期向量）
        stale_embedding_col = self._content_columns_changed(columns, list(values.keys()))
        if stale_embedding_col and stale_embedding_col not in assignments:
            assignments[stale_embedding_col] = None

        set_sql = ", ".join(f'"{n}" = ?' for n in assignments)
        cursor = await conn.execute(
            f'UPDATE "{table}" SET {set_sql} WHERE rowid = ?',
            (*assignments.values(), rowid),
        )
        await conn.commit()
        if cursor.rowcount == 0:
            raise DatabaseError(f"行不存在: rowid={rowid}", status_code=404)
        log(f"数据库管理: 更新行 {db_id}/{table} rowid={rowid} 列={list(values.keys())}", "DEBUG", tag="数据库")

    async def delete_row(self, db_id: str, table: str, rowid: int) -> None:
        conn = await self._get_conn(db_id)
        await self._require_writable(conn, table)
        cursor = await conn.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (rowid,))
        await conn.commit()
        if cursor.rowcount == 0:
            raise DatabaseError(f"行不存在: rowid={rowid}", status_code=404)
        log(f"数据库管理: 删除行 {db_id}/{table} rowid={rowid}", "DEBUG", tag="数据库")

    # ------------------------------------------------------------------
    # 只读 SQL 控制台
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_readonly_sql(sql: str) -> str:
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
        conn = await self._get_conn(db_id)
        safe_sql = self._validate_readonly_sql(sql)
        started = time.time()

        async def _do() -> Tuple[List[str], List[Dict[str, Any]]]:
            cursor = await conn.execute(safe_sql)
            rows = await cursor.fetchmany(_QUERY_MAX_ROWS)
            col_names = [d[0] for d in cursor.description] if cursor.description else []
            return col_names, [
                {name: _serialize_value(r[name], name) for name in col_names} for r in rows
            ]

        try:
            columns, rows = await asyncio.wait_for(_do(), timeout=_QUERY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise DatabaseError(f"查询超时（{_QUERY_TIMEOUT_SECONDS:.0f}s）", status_code=400) from None
        except DatabaseError:
            raise
        except Exception as exc:
            raise DatabaseError(f"查询失败: {exc}", status_code=400) from exc
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "elapsed_ms": round((time.time() - started) * 1000, 1),
            "truncated": len(rows) >= _QUERY_MAX_ROWS,
        }
