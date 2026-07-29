"""外部 SQL 数据库连接管理 — 注册表 + 只读适配器（PostgreSQL / MySQL）。

为 WebUI「数据管理」页提供外部只读数据源：
- 连接注册表持久化到 ``config/db_connections.json``（密码支持 ``${ENV_VAR}`` 引用）
- 适配器统一接口：test / list_tables / table_schema / browse_rows / run_query
- 全程只读：会话级 READ ONLY + 语句首关键字白名单，无行级写操作
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from core.config import expand_env_refs
from core.log import log
from core.path import ConfigPaths

_ENGINES = ("postgresql", "mysql")
_DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306}
_CONNECT_TIMEOUT = 10.0
_QUERY_MAX_ROWS = 500

# 外部源只读语句允许的首关键字（PRAGMA 为 SQLite 专有，不在此列）
_EXT_ALLOWED_KEYWORDS = ("select", "with", "explain")


class DbConnection(BaseModel):
    """一条外部数据库连接配置。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str
    engine: str
    host: str = "127.0.0.1"
    port: int = 0  # 0 → 引擎默认端口
    database: str
    username: str = ""
    password: str = ""  # 支持 ${ENV_VAR} 引用
    created_at: float = Field(default_factory=time.time)

    def effective_port(self) -> int:
        return self.port or _DEFAULT_PORTS.get(self.engine, 0)

    def resolved_password(self) -> str:
        return str(expand_env_refs(self.password))

    def to_public_dict(self) -> Dict[str, Any]:
        """脱敏展示（密码不回传）。"""
        d = self.model_dump()
        d["password"] = "****" if self.password else ""
        d["has_password"] = bool(self.password)
        return d


def _validate_conn_data(data: Dict[str, Any]) -> None:
    from services.database import DatabaseError

    engine = data.get("engine", "")
    if engine not in _ENGINES:
        raise DatabaseError(f"不支持的数据库引擎: {engine}（支持: {', '.join(_ENGINES)}）", status_code=400)
    if not str(data.get("name", "")).strip():
        raise DatabaseError("连接名称不能为空", status_code=400)
    if not str(data.get("database", "")).strip():
        raise DatabaseError("数据库名不能为空", status_code=400)
    port = data.get("port", 0)
    if port is not None and not (0 <= int(port) <= 65535):
        raise DatabaseError("端口号超出范围", status_code=400)


def _validate_readonly_sql(sql: str) -> str:
    """外部源只读语句校验：单语句 + 首关键字白名单 + 自动补 LIMIT。"""
    from services.database import DatabaseError

    text = sql.strip().rstrip(";").strip()
    if not text:
        raise DatabaseError("SQL 为空", status_code=400)
    if ";" in text:
        raise DatabaseError("只允许单条语句", status_code=400)
    first = re.match(r"^\s*(?:--[^\n]*\n\s*)*(\w+)", text)
    keyword = (first.group(1).lower() if first else "")
    if keyword not in _EXT_ALLOWED_KEYWORDS:
        raise DatabaseError(
            f"只允许只读语句（{', '.join(k.upper() for k in _EXT_ALLOWED_KEYWORDS)}）",
            status_code=403,
        )
    if keyword in ("select", "with") and not re.search(r"\blimit\b", text, re.IGNORECASE):
        text = f"{text} LIMIT {_QUERY_MAX_ROWS}"
    return text


# ----------------------------------------------------------------------
# 适配器
# ----------------------------------------------------------------------

class ExternalAdapter:
    """只读外部库适配器基类（统一接口 + 共用浏览/查询逻辑）。"""

    quote_left = '"'
    quote_right = '"'

    def __init__(self, conn: DbConnection) -> None:
        self._conn = conn

    # ---------------- 引擎实现 ----------------

    async def _fetch(self, sql: str, params: Tuple[Any, ...] = ()) -> Tuple[List[str], List[Tuple[Any, ...]]]:
        """执行只读查询，返回 (列名, 行元组列表)。由子类实现。"""
        raise NotImplementedError

    async def _server_version(self) -> str:
        raise NotImplementedError

    async def _list_table_entries(self) -> List[Dict[str, Any]]:
        """返回 [{name, type, row_estimate}]，由子类实现。"""
        raise NotImplementedError

    async def _list_columns(self, table: str) -> List[Dict[str, Any]]:
        """返回 [{cid, name, type, notnull, default, pk}]，由子类实现。"""
        raise NotImplementedError

    async def _list_indexes(self, table: str) -> List[Dict[str, Any]]:
        """返回 [{name, unique, columns}]，由子类实现。"""
        raise NotImplementedError

    async def close(self) -> None:
        """关闭底层连接/池。由子类实现。"""

    # ---------------- 统一接口 ----------------

    def _quote(self, ident: str) -> str:
        return f"{self.quote_left}{ident}{self.quote_right}"

    async def test(self) -> Dict[str, Any]:
        started = time.time()
        version = await self._server_version()
        return {
            "ok": True,
            "version": version,
            "latency_ms": round((time.time() - started) * 1000, 1),
        }

    async def list_tables(self) -> List[Dict[str, Any]]:
        tables: List[Dict[str, Any]] = []
        for entry in await self._list_table_entries():
            tbl_type = entry["type"]
            tables.append({
                "name": entry["name"],
                "type": "view" if "VIEW" in tbl_type.upper() else "table",
                "virtual": False,
                "shadow": False,
                "readonly": True,
                "row_count": entry.get("row_estimate", -1),
                "column_count": 0,
            })
        return tables

    async def table_schema(self, table: str) -> Dict[str, Any]:
        columns = await self._list_columns(table)
        if not columns:
            from services.database import DatabaseError
            raise DatabaseError(f"表不存在或无列: {table}", status_code=404)
        return {
            "table": table,
            "type": "table",
            "readonly": True,
            "ddl": "",
            "columns": columns,
            "indexes": await self._list_indexes(table),
        }

    async def browse_rows(
        self,
        table: str,
        *,
        page: int = 1,
        page_size: int = 50,
        sort: Optional[str] = None,
        order: str = "asc",
        filter_col: Optional[str] = None,
        filter_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        from services.database import _serialize_value

        columns = await self._list_columns(table)
        if not columns:
            from services.database import DatabaseError
            raise DatabaseError(f"表不存在或无列: {table}", status_code=404)
        col_names = [c["name"] for c in columns]
        valid = set(col_names)

        where_sql = ""
        params: List[Any] = []
        if filter_col and filter_text:
            if filter_col not in valid:
                from services.database import DatabaseError
                raise DatabaseError(f"未知列: {filter_col}", status_code=400)
            where_sql = f"WHERE CAST({self._quote(filter_col)} AS {self._text_cast}) LIKE {self._placeholder(1)}"
            params.append(f"%{filter_text}%")

        order_sql = ""
        if sort:
            if sort not in valid:
                from services.database import DatabaseError
                raise DatabaseError(f"未知列: {sort}", status_code=400)
            direction = "DESC" if order.lower() == "desc" else "ASC"
            order_sql = f"ORDER BY {self._quote(sort)} {direction}"

        _, count_rows = await self._fetch(
            f"SELECT COUNT(*) FROM {self._quote(table)} {where_sql}", tuple(params)
        )
        total = int(count_rows[0][0]) if count_rows else 0

        page = max(1, page)
        page_size = max(1, min(page_size, 200))
        offset = (page - 1) * page_size
        _, rows = await self._fetch(
            f"SELECT * FROM {self._quote(table)} {where_sql} {order_sql} "
            f"LIMIT {page_size} OFFSET {offset}",
            tuple(params),
        )
        items = [
            {
                "__rowid__": offset + idx + 1,
                "values": {name: _serialize_value(row[i], name) for i, name in enumerate(col_names)},
            }
            for idx, row in enumerate(rows)
        ]
        pages = (total + page_size - 1) // page_size if total else 0
        return {
            "items": items,
            "columns": columns,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "readonly": True,
        }

    async def run_query(self, sql: str) -> Dict[str, Any]:
        from services.database import DatabaseError, _serialize_value

        safe_sql = _validate_readonly_sql(sql)
        started = time.time()
        try:
            columns, rows = await asyncio.wait_for(self._fetch(safe_sql), timeout=_CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            raise DatabaseError(f"查询超时（{_CONNECT_TIMEOUT:.0f}s）", status_code=400) from None
        except DatabaseError:
            raise
        except Exception as exc:
            raise DatabaseError(f"查询失败: {exc}", status_code=400) from exc
        result_rows = [
            {name: _serialize_value(row[i], name) for i, name in enumerate(columns)}
            for row in rows[:_QUERY_MAX_ROWS]
        ]
        return {
            "columns": columns,
            "rows": result_rows,
            "row_count": len(result_rows),
            "elapsed_ms": round((time.time() - started) * 1000, 1),
            "truncated": len(rows) >= _QUERY_MAX_ROWS,
        }

    # 子类覆盖：文本 CAST 目标类型与占位符风格
    _text_cast = "TEXT"

    def _placeholder(self, index: int) -> str:
        return "%s"


class PostgresAdapter(ExternalAdapter):
    """PostgreSQL 只读适配器（asyncpg，事务级 READ ONLY）。"""

    _text_cast = "TEXT"

    def __init__(self, conn: DbConnection) -> None:
        super().__init__(conn)
        self._pool: Any = None
        self._lock = asyncio.Lock()

    def _placeholder(self, index: int) -> str:
        return f"${index}"

    async def _get_pool(self) -> Any:
        async with self._lock:
            if self._pool is None:
                import asyncpg

                c = self._conn
                self._pool = await asyncpg.create_pool(
                    host=c.host,
                    port=c.effective_port(),
                    database=c.database,
                    user=c.username or None,
                    password=c.resolved_password() or None,
                    min_size=1,
                    max_size=2,
                    timeout=_CONNECT_TIMEOUT,
                )
            return self._pool

    async def close(self) -> None:
        async with self._lock:
            if self._pool is not None:
                try:
                    await self._pool.close()
                except Exception:
                    pass
                self._pool = None

    async def _fetch(self, sql: str, params: Tuple[Any, ...] = ()) -> Tuple[List[str], List[Tuple[Any, ...]]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction(readonly=True):
                stmt = await conn.prepare(sql)
                records = await stmt.fetch(*params)
                columns = [a.name for a in stmt.get_attributes()]
                return columns, [tuple(r.values()) for r in records]

    async def _server_version(self) -> str:
        _, rows = await self._fetch("SELECT version()")
        return str(rows[0][0]) if rows else ""

    async def _list_table_entries(self) -> List[Dict[str, Any]]:
        _, rows = await self._fetch(
            "SELECT c.relname, c.relkind, GREATEST(c.reltuples, 0)::bigint "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'v') "
            "ORDER BY c.relname"
        )
        return [
            {
                "name": r[0],
                "type": "VIEW" if r[1] == "v" else "BASE TABLE",
                "row_estimate": int(r[2]),
            }
            for r in rows
        ]

    async def _list_columns(self, table: str) -> List[Dict[str, Any]]:
        _, rows = await self._fetch(
            "SELECT c.ordinal_position, c.column_name, c.data_type, c.is_nullable, "
            "c.column_default, EXISTS ("
            "  SELECT 1 FROM information_schema.table_constraints tc "
            "  JOIN information_schema.key_column_usage kcu "
            "    ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema "
            "  WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = c.table_schema "
            "    AND tc.table_name = c.table_name AND kcu.column_name = c.column_name"
            ") AS is_pk "
            "FROM information_schema.columns c "
            "WHERE c.table_schema = current_schema() AND c.table_name = $1 "
            "ORDER BY c.ordinal_position",
            (table,),
        )
        return [
            {
                "cid": r[0],
                "name": r[1],
                "type": str(r[2]).upper(),
                "notnull": r[3] == "NO",
                "default": r[4],
                "pk": bool(r[5]),
            }
            for r in rows
        ]

    async def _list_indexes(self, table: str) -> List[Dict[str, Any]]:
        _, rows = await self._fetch(
            "SELECT i.relname, ix.indisunique, "
            "ARRAY(SELECT a.attname FROM unnest(ix.indkey) k "
            "  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k) AS cols "
            "FROM pg_index ix "
            "JOIN pg_class t ON t.oid = ix.indrelid "
            "JOIN pg_class i ON i.oid = ix.indexrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = current_schema() AND t.relname = $1",
            (table,),
        )
        return [{"name": r[0], "unique": bool(r[1]), "columns": list(r[2])} for r in rows]


class MysqlAdapter(ExternalAdapter):
    """MySQL 只读适配器（aiomysql，会话级 READ ONLY + 语句白名单）。"""

    quote_left = "`"
    quote_right = "`"
    _text_cast = "CHAR"

    def __init__(self, conn: DbConnection) -> None:
        super().__init__(conn)
        self._conn_obj: Any = None
        self._lock = asyncio.Lock()

    async def _get_conn(self) -> Any:
        async with self._lock:
            if self._conn_obj is None:
                import aiomysql

                c = self._conn
                self._conn_obj = await aiomysql.connect(
                    host=c.host,
                    port=c.effective_port(),
                    db=c.database,
                    user=c.username or None,
                    password=c.resolved_password() or None,
                    connect_timeout=int(_CONNECT_TIMEOUT),
                    autocommit=True,
                )
                async with self._conn_obj.cursor() as cur:
                    await cur.execute("SET SESSION TRANSACTION READ ONLY")
            return self._conn_obj

    async def close(self) -> None:
        async with self._lock:
            if self._conn_obj is not None:
                try:
                    self._conn_obj.close()
                except Exception:
                    pass
                self._conn_obj = None

    async def _fetch(self, sql: str, params: Tuple[Any, ...] = ()) -> Tuple[List[str], List[Tuple[Any, ...]]]:
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            await cur.execute(sql, params or None)
            rows = await cur.fetchall()
            columns = [d[0] for d in cur.description] if cur.description else []
            return columns, list(rows)

    async def _server_version(self) -> str:
        _, rows = await self._fetch("SELECT VERSION()")
        return str(rows[0][0]) if rows else ""

    async def _list_table_entries(self) -> List[Dict[str, Any]]:
        _, rows = await self._fetch(
            "SELECT table_name, table_type, COALESCE(table_rows, 0) "
            "FROM information_schema.tables WHERE table_schema = DATABASE() "
            "ORDER BY table_name"
        )
        return [
            {"name": r[0], "type": r[1], "row_estimate": int(r[2])}
            for r in rows
        ]

    async def _list_columns(self, table: str) -> List[Dict[str, Any]]:
        _, rows = await self._fetch(
            "SELECT ordinal_position, column_name, data_type, is_nullable, "
            "column_default, column_key "
            "FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s "
            "ORDER BY ordinal_position",
            (table,),
        )
        return [
            {
                "cid": r[0],
                "name": r[1],
                "type": str(r[2]).upper(),
                "notnull": r[3] == "NO",
                "default": r[4],
                "pk": r[5] == "PRI",
            }
            for r in rows
        ]

    async def _list_indexes(self, table: str) -> List[Dict[str, Any]]:
        _, rows = await self._fetch(
            "SELECT index_name, NOT non_unique, column_name "
            "FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = %s "
            "ORDER BY index_name, seq_in_index",
            (table,),
        )
        grouped: Dict[str, Dict[str, Any]] = {}
        for name, unique, col in rows:
            entry = grouped.setdefault(name, {"name": name, "unique": bool(unique), "columns": []})
            entry["columns"].append(col)
        return list(grouped.values())


_ADAPTERS = {"postgresql": PostgresAdapter, "mysql": MysqlAdapter}


# ----------------------------------------------------------------------
# 连接注册表
# ----------------------------------------------------------------------

class ConnectionStore:
    """外部连接注册表：JSON 持久化 + 适配器缓存。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path or Path(ConfigPaths.DB_CONNECTIONS)
        self._items: Dict[str, DbConnection] = {}
        self._adapters: Dict[str, ExternalAdapter] = {}
        self._file_lock = threading.Lock()
        self.reload()

    def reload(self) -> None:
        with self._file_lock:
            self._items.clear()
            if not self._path.exists():
                return
            try:
                raw = json.loads(self._path.read_text("utf-8"))
                for item in raw.get("connections", []):
                    conn = DbConnection(**item)
                    self._items[conn.id] = conn
            except Exception as exc:
                log(f"外部连接配置加载失败: {exc}", "WARNING", tag="数据库")

    def _save(self) -> None:
        with self._file_lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"connections": [c.model_dump() for c in self._items.values()]}
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # ---------------- CRUD ----------------

    def list(self) -> List[DbConnection]:
        return list(self._items.values())

    def list_public(self) -> List[Dict[str, Any]]:
        return [c.to_public_dict() for c in self._items.values()]

    def get(self, conn_id: str) -> DbConnection:
        from services.database import DatabaseError

        conn = self._items.get(conn_id)
        if conn is None:
            raise DatabaseError(f"外部连接不存在: {conn_id}", status_code=404)
        return conn

    def add(self, data: Dict[str, Any]) -> DbConnection:
        _validate_conn_data(data)
        data = {k: v for k, v in data.items() if k != "id"}
        conn = DbConnection(**data)
        self._items[conn.id] = conn
        self._save()
        log(f"外部连接已创建: {conn.name} ({conn.engine} {conn.host}:{conn.effective_port()})", tag="数据库")
        return conn

    def update(self, conn_id: str, data: Dict[str, Any]) -> DbConnection:
        existing = self.get(conn_id)
        merged = existing.model_dump()
        for key, val in data.items():
            if key in ("id", "created_at"):
                continue
            # 密码留空或仍是脱敏形式 → 保留旧值
            if key == "password" and (not val or (isinstance(val, str) and "****" in val)):
                continue
            merged[key] = val
        _validate_conn_data(merged)
        conn = DbConnection(**merged)
        self._items[conn_id] = conn
        self._save()
        # 配置变更后旧适配器失效
        self._drop_adapter(conn_id)
        return conn

    def delete(self, conn_id: str) -> None:
        self.get(conn_id)
        self._items.pop(conn_id, None)
        self._save()
        self._drop_adapter(conn_id)

    def _drop_adapter(self, conn_id: str) -> None:
        adapter = self._adapters.pop(conn_id, None)
        if adapter is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(adapter.close())
            except RuntimeError:
                pass

    # ---------------- 适配器与测试 ----------------

    async def adapter(self, conn_id: str) -> ExternalAdapter:
        cached = self._adapters.get(conn_id)
        if cached is not None:
            return cached
        conn = self.get(conn_id)
        adapter = _ADAPTERS[conn.engine](conn)
        self._adapters[conn_id] = adapter
        return adapter

    async def test(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """测试连接（支持未保存的草稿配置）。"""
        from services.database import DatabaseError

        _validate_conn_data(data)
        draft = DbConnection(**{k: v for k, v in data.items() if k != "id"})
        # 草稿的脱敏密码 → 尝试沿用已保存的同 id 连接密码
        if isinstance(data.get("password"), str) and "****" in data["password"] and data.get("id"):
            try:
                draft.password = self.get(str(data["id"])).password
            except DatabaseError:
                pass
        adapter: ExternalAdapter = _ADAPTERS[draft.engine](draft)
        started = time.time()
        try:
            return await asyncio.wait_for(adapter.test(), timeout=_CONNECT_TIMEOUT + 2)
        except asyncio.TimeoutError:
            raise DatabaseError("连接超时", status_code=400) from None
        except DatabaseError:
            raise
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "latency_ms": round((time.time() - started) * 1000, 1),
            }
        finally:
            try:
                await adapter.close()
            except Exception:
                pass

    async def list_source_entries(self) -> List[Dict[str, Any]]:
        """供 DatabaseService.list_databases 追加的外部源条目（带连通性探测）。"""
        entries: List[Dict[str, Any]] = []
        for conn in self._items.values():
            entry: Dict[str, Any] = {
                "id": f"ext:{conn.id}",
                "name": conn.name,
                "description": f"{conn.engine} · {conn.host}:{conn.effective_port()}/{conn.database}",
                "path": "",
                "exists": False,
                "external": True,
                "engine": conn.engine,
                "size_bytes": 0,
                "table_count": 0,
            }
            try:
                adapter = await self.adapter(conn.id)
                result = await asyncio.wait_for(adapter.test(), timeout=_CONNECT_TIMEOUT)
                entry["exists"] = True
                tables = await adapter.list_tables()
                entry["table_count"] = len(tables)
            except Exception as exc:
                entry["error"] = str(exc)
            entries.append(entry)
        return entries

    async def close_all(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.close()
            except Exception:
                pass
        self._adapters.clear()


_store: Optional[ConnectionStore] = None


def get_connection_store() -> ConnectionStore:
    global _store
    if _store is None:
        _store = ConnectionStore()
    return _store
