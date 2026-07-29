"""数据库管理 API 路由 — WebUI「数据管理」页的数据库 Tab。

DatabaseError / MigrationError 由 web.server 装配处注册的
exception_handler 统一转换为 HTTP 响应，路由内不再逐个 try/except。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from services.data_migration import (
    check_target,
    get_location,
    migration_status,
    start_migration,
)
from services.database import DatabaseService
from services.db_connections import get_connection_store

router = APIRouter(prefix="/database", tags=["database"])

_db_svc = DatabaseService()


class RowValuesRequest(BaseModel):
    """行写入请求（插入/更新）。"""

    values: Dict[str, Any] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    """只读 SQL 查询请求。"""

    sql: str = Field(min_length=1, max_length=10000)


class OptimizeRequest(BaseModel):
    """维护操作请求。"""

    actions: List[str] = Field(min_length=1)


class ConnectionUpsert(BaseModel):
    """外部连接创建/更新请求。"""

    name: str
    engine: str
    host: str = "127.0.0.1"
    port: int = 0
    database: str
    username: str = ""
    password: str = ""


class ConnectionTest(BaseModel):
    """连接测试请求（支持草稿）。"""

    id: Optional[str] = None
    name: str = "test"
    engine: str
    host: str = "127.0.0.1"
    port: int = 0
    database: str
    username: str = ""
    password: str = ""


class TargetCheckRequest(BaseModel):
    """迁移目标校验请求。"""

    target: str


class MigrateRequest(BaseModel):
    """启动迁移请求。"""

    target: str


# ----------------------------------------------------------------------
# 外部连接管理（声明在 /{db_id} 之前，避免被参数路由捕获）
# ----------------------------------------------------------------------


@router.get("/connections")
async def list_connections() -> Dict[str, Any]:
    return {"items": get_connection_store().list_public()}


@router.post("/connections", status_code=201)
async def create_connection(body: ConnectionUpsert) -> Dict[str, Any]:
    conn = get_connection_store().add(body.model_dump())
    return conn.to_public_dict()


@router.put("/connections/{conn_id}")
async def update_connection(conn_id: str, body: ConnectionUpsert) -> Dict[str, Any]:
    conn = get_connection_store().update(conn_id, body.model_dump())
    return conn.to_public_dict()


@router.delete("/connections/{conn_id}")
async def delete_connection(conn_id: str) -> Dict[str, Any]:
    get_connection_store().delete(conn_id)
    return {"success": True}


@router.post("/connections/test")
async def test_connection(body: ConnectionTest) -> Dict[str, Any]:
    return await get_connection_store().test(body.model_dump())


# ----------------------------------------------------------------------
# 数据位置与迁移
# ----------------------------------------------------------------------


@router.get("/location")
async def data_location() -> Dict[str, Any]:
    return get_location()


@router.post("/location/check")
async def data_location_check(body: TargetCheckRequest) -> Dict[str, Any]:
    return check_target(body.target)


@router.post("/migrate")
async def data_migrate(body: MigrateRequest) -> Dict[str, Any]:
    return start_migration(body.target)


@router.get("/migrate/status")
async def data_migrate_status() -> Dict[str, Any]:
    return migration_status()


# ----------------------------------------------------------------------
# 库清单与维护
# ----------------------------------------------------------------------


@router.get("/databases")
async def list_databases() -> Dict[str, Any]:
    return {"items": await _db_svc.list_databases()}


@router.get("/{db_id}/health")
async def database_health(db_id: str) -> Dict[str, Any]:
    return await _db_svc.database_health(db_id)


@router.post("/{db_id}/backup")
async def backup_database(db_id: str) -> FileResponse:
    info = await _db_svc.backup_database(db_id)
    return FileResponse(info["path"], filename=info["filename"])


@router.post("/{db_id}/optimize")
async def optimize_database(db_id: str, body: OptimizeRequest) -> Dict[str, Any]:
    return await _db_svc.optimize_database(db_id, body.actions)


@router.get("/{db_id}/tables")
async def list_tables(
    db_id: str,
    include_shadow: bool = Query(False),
) -> Dict[str, Any]:
    return {"items": await _db_svc.list_tables(db_id, include_shadow=include_shadow)}


@router.get("/{db_id}/tables/{table}/schema")
async def table_schema(db_id: str, table: str) -> Dict[str, Any]:
    return await _db_svc.table_schema(db_id, table)


@router.get("/{db_id}/tables/{table}/rows")
async def browse_rows(
    db_id: str,
    table: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: Optional[str] = Query(None),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    filter_col: Optional[str] = Query(None),
    filter_text: Optional[str] = Query(None),
) -> Dict[str, Any]:
    return await _db_svc.browse_rows(
        db_id,
        table,
        page=page,
        page_size=page_size,
        sort=sort,
        order=order,
        filter_col=filter_col,
        filter_text=filter_text,
    )


@router.get("/{db_id}/tables/{table}/rows/{rowid}")
async def get_row(db_id: str, table: str, rowid: int) -> Dict[str, Any]:
    return await _db_svc.get_row(db_id, table, rowid)


@router.post("/{db_id}/tables/{table}/rows")
async def insert_row(db_id: str, table: str, body: RowValuesRequest) -> Dict[str, Any]:
    return await _db_svc.insert_row(db_id, table, body.values)


@router.put("/{db_id}/tables/{table}/rows/{rowid}")
async def update_row(db_id: str, table: str, rowid: int, body: RowValuesRequest) -> Dict[str, Any]:
    await _db_svc.update_row(db_id, table, rowid, body.values)
    return {"success": True}


@router.delete("/{db_id}/tables/{table}/rows/{rowid}")
async def delete_row(db_id: str, table: str, rowid: int) -> Dict[str, Any]:
    await _db_svc.delete_row(db_id, table, rowid)
    return {"success": True}


@router.post("/{db_id}/query")
async def run_query(db_id: str, body: QueryRequest) -> Dict[str, Any]:
    return await _db_svc.run_query(db_id, body.sql)
