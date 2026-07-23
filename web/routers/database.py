"""数据库管理 API 路由 — WebUI「数据管理」页的数据库 Tab。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from services.database import DatabaseError, DatabaseService

router = APIRouter(prefix="/database", tags=["database"])

_db_svc = DatabaseService()


class RowValuesRequest(BaseModel):
    """行写入请求（插入/更新）。"""

    values: Dict[str, Any] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    """只读 SQL 查询请求。"""

    sql: str = Field(min_length=1, max_length=10000)


def _handle_error(exc: DatabaseError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/databases")
async def list_databases() -> Dict[str, Any]:
    return {"items": await _db_svc.list_databases()}


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
