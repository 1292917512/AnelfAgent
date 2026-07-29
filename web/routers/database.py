"""数据库管理 API 路由 — WebUI「数据管理」页的数据库 Tab。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from services.database import DatabaseError, DatabaseService
from services.data_migration import (
    MigrationError,
    check_target,
    get_location,
    migration_status,
    start_migration,
)

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


def _handle_error(exc: DatabaseError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _handle_migration_error(exc: MigrationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


# ----------------------------------------------------------------------
# 外部连接管理（声明在 /{db_id} 之前，避免被参数路由捕获）
# ----------------------------------------------------------------------


@router.get("/connections")
async def list_connections() -> Dict[str, Any]:
    from services.db_connections import get_connection_store
    return {"items": get_connection_store().list_public()}


@router.post("/connections", status_code=201)
async def create_connection(body: ConnectionUpsert) -> Dict[str, Any]:
    from services.db_connections import get_connection_store
    try:
        conn = get_connection_store().add(body.model_dump())
        return conn.to_public_dict()
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.put("/connections/{conn_id}")
async def update_connection(conn_id: str, body: ConnectionUpsert) -> Dict[str, Any]:
    from services.db_connections import get_connection_store
    try:
        conn = get_connection_store().update(conn_id, body.model_dump())
        return conn.to_public_dict()
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.delete("/connections/{conn_id}")
async def delete_connection(conn_id: str) -> Dict[str, Any]:
    from services.db_connections import get_connection_store
    try:
        get_connection_store().delete(conn_id)
        return {"success": True}
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.post("/connections/test")
async def test_connection(body: ConnectionTest) -> Dict[str, Any]:
    from services.db_connections import get_connection_store
    try:
        return await get_connection_store().test(body.model_dump())
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


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
    try:
        return start_migration(body.target)
    except MigrationError as exc:
        raise _handle_migration_error(exc) from exc


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
    try:
        return await _db_svc.database_health(db_id)
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.post("/{db_id}/backup")
async def backup_database(db_id: str) -> FileResponse:
    try:
        info = await _db_svc.backup_database(db_id)
    except DatabaseError as exc:
        raise _handle_error(exc) from exc
    return FileResponse(info["path"], filename=info["filename"])


@router.post("/{db_id}/optimize")
async def optimize_database(db_id: str, body: OptimizeRequest) -> Dict[str, Any]:
    try:
        return await _db_svc.optimize_database(db_id, body.actions)
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.get("/{db_id}/tables")
async def list_tables(
    db_id: str,
    include_shadow: bool = Query(False),
) -> Dict[str, Any]:
    try:
        return {"items": await _db_svc.list_tables(db_id, include_shadow=include_shadow)}
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.get("/{db_id}/tables/{table}/schema")
async def table_schema(db_id: str, table: str) -> Dict[str, Any]:
    try:
        return await _db_svc.table_schema(db_id, table)
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


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
    try:
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
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.get("/{db_id}/tables/{table}/rows/{rowid}")
async def get_row(db_id: str, table: str, rowid: int) -> Dict[str, Any]:
    try:
        return await _db_svc.get_row(db_id, table, rowid)
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.post("/{db_id}/tables/{table}/rows")
async def insert_row(db_id: str, table: str, body: RowValuesRequest) -> Dict[str, Any]:
    try:
        return await _db_svc.insert_row(db_id, table, body.values)
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.put("/{db_id}/tables/{table}/rows/{rowid}")
async def update_row(db_id: str, table: str, rowid: int, body: RowValuesRequest) -> Dict[str, Any]:
    try:
        await _db_svc.update_row(db_id, table, rowid, body.values)
        return {"success": True}
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.delete("/{db_id}/tables/{table}/rows/{rowid}")
async def delete_row(db_id: str, table: str, rowid: int) -> Dict[str, Any]:
    try:
        await _db_svc.delete_row(db_id, table, rowid)
        return {"success": True}
    except DatabaseError as exc:
        raise _handle_error(exc) from exc


@router.post("/{db_id}/query")
async def run_query(db_id: str, body: QueryRequest) -> Dict[str, Any]:
    try:
        return await _db_svc.run_query(db_id, body.sql)
    except DatabaseError as exc:
        raise _handle_error(exc) from exc
