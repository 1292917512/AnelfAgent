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

# 维护操作白名单与阈值
_OPTIMIZE_ACTIONS = ("checkpoint", "vacuum", "analyze")
_WAL_WARN_BYTES = 32 * 1024 * 1024  # WAL 超过 32MB 建议 checkpoint
_FRAGMENT_WARN_RATIO = 0.2  # 空闲页占比超过 20% 建议 VACUUM
_FRAGMENT_MIN_PAGES = 100  # 小库不报碎片化（无意义）


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
        "voiceprints": {
            "name": "音源库",
            "path": f"{stem}_voiceprints.sqlite3",
            "description": "说话人档案 / 声纹样本池 / 语音转写片段 / 已同步文件登记",
        },
        "skill_vectors": {
            "name": "技能向量库",
            "path": f"{stem}_skill_vectors.sqlite3",
            "description": "技能语义向量（模型+文本hash 双因子校验；重建操作见技能库页）",
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
                log("_serialize_value 异常已忽略", "DEBUG")
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
            log("_serialize_value 异常已忽略", "DEBUG")
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
                log("_full_value 异常已忽略", "DEBUG")
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
                        log("_get_conn 异常已忽略", "DEBUG")
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
                log("close_all 异常已忽略", "DEBUG")
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
                "external": False,
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
                    log("list_databases 异常已忽略", "DEBUG")
                except Exception as exc:
                    entry["error"] = str(exc)
            result.append(entry)
        # 外部连接（PostgreSQL / MySQL，只读数据源）
        try:
            from services.db_connections import get_connection_store
            result.extend(await get_connection_store().list_source_entries())
        except Exception as exc:
            log(f"外部连接清单获取失败: {exc}", "DEBUG", tag="数据库")
        return result

    @staticmethod
    def _is_external(db_id: str) -> bool:
        return db_id.startswith("ext:")

    @staticmethod
    async def _external_adapter(db_id: str) -> Any:
        from services.db_connections import get_connection_store
        return await get_connection_store().adapter(db_id[len("ext:"):])

    async def list_tables(self, db_id: str, include_shadow: bool = False) -> List[Dict[str, Any]]:
        if self._is_external(db_id):
            return await (await self._external_adapter(db_id)).list_tables()
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
        if self._is_external(db_id):
            return await (await self._external_adapter(db_id)).table_schema(table)
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
        if self._is_external(db_id):
            return await (await self._external_adapter(db_id)).browse_rows(
                table,
                page=page,
                page_size=page_size,
                sort=sort,
                order=order,
                filter_col=filter_col,
                filter_text=filter_text,
            )
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
        if self._is_external(db_id):
            raise DatabaseError("外部连接无 rowid 语义，不支持单行详情", status_code=403)
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
        self._require_local(db_id)
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
        self._require_local(db_id)
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
        self._require_local(db_id)
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
        if self._is_external(db_id):
            return await (await self._external_adapter(db_id)).run_query(sql)
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
            # shield：wait_for 的取消无法中断 aiosqlite 工作线程中正在执行的
            # sqlite 查询；超时后放弃等待（查询仍在后台执行直至完成），并把当前
            # 连接移出缓存异步关闭回收，后续请求自动重建新连接。
            columns, rows = await asyncio.wait_for(asyncio.shield(_do()), timeout=_QUERY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            stale = self._connections.pop(db_id, None)
            if stale is not None:
                async def _close_stale() -> None:
                    try:
                        await stale.close()
                    except Exception:
                        log("_close_stale 异常已忽略", "DEBUG")
                asyncio.get_running_loop().create_task(_close_stale())
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

    # ------------------------------------------------------------------
    # 健康概览 / 备份 / 优化（仅本地 SQLite）
    # ------------------------------------------------------------------

    @staticmethod
    def _require_local(db_id: str) -> None:
        """外部连接（ext:<id>）为只读数据源，不支持维护/写操作。"""
        if db_id.startswith("ext:"):
            raise DatabaseError("外部连接为只读数据源，不支持该操作", status_code=403)

    async def database_health(self, db_id: str) -> Dict[str, Any]:
        """库健康概览：体积 / WAL / 碎片化 / 表规模 / 维护建议。"""
        self._require_local(db_id)
        conn = await self._get_conn(db_id)
        path = self._db_path(db_id)

        size_bytes = os.path.getsize(path)
        wal_path = f"{path}-wal"
        wal_bytes = os.path.getsize(wal_path) if os.path.isfile(wal_path) else 0

        page_count = (await (await conn.execute("PRAGMA page_count")).fetchone())[0]
        freelist_count = (await (await conn.execute("PRAGMA freelist_count")).fetchone())[0]
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
        self._require_local(db_id)
        path = self._db_path(db_id)
        from core.path import ConfigPaths, project_root
        backup_dir = Path(project_root()) / ConfigPaths.MEMORY_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{db_id}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
        dest = backup_dir / filename

        started = time.time()
        src = await aiosqlite.connect(path)
        try:
            target = await aiosqlite.connect(str(dest))
            try:
                await src.backup(target)
            finally:
                await target.close()
        finally:
            await src.close()

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
        self._require_local(db_id)
        bad = [a for a in actions if a not in _OPTIMIZE_ACTIONS]
        if bad:
            raise DatabaseError(f"未知维护操作: {', '.join(bad)}", status_code=400)
        if not actions:
            raise DatabaseError("维护操作为空", status_code=400)

        conn = await self._get_conn(db_id)
        results: List[Dict[str, Any]] = []
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
