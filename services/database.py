"""数据库管理门面：目录、能力检查和适配器路由；不实现引擎 SQL 或业务表规则。"""

from __future__ import annotations

import os
import time
from typing import Any

from core.database import DatabaseCapability
from core.log import log
from services.database_catalog import database_registry, require_database
from services.database_editing import DatabaseRowEditor
from services.database_sqlite import SqliteDatabaseAdapter
from services.db_common import DatabaseError
from services.db_connections import ExternalAdapter, get_connection_store


class DatabaseService:
    """统一管理入口；组件目录来自卷声明，连接和行编辑由专职对象负责。"""

    def __init__(self) -> None:
        self._sqlite = SqliteDatabaseAdapter()
        self._editor = DatabaseRowEditor(self._sqlite)

    async def close_all(self) -> None:
        await self._sqlite.close_all()

    def _local(self, db_id: str, capability: DatabaseCapability) -> SqliteDatabaseAdapter:
        if db_id.startswith("ext:"):
            raise DatabaseError("外部连接为只读数据源，不支持该操作", status_code=403)
        entry = require_database(db_id, capability)
        if entry.component.engine != "sqlite":
            raise DatabaseError(f"该引擎未提供通用数据库操作: {entry.component.engine}", status_code=403)
        return self._sqlite

    async def _reader(self, db_id: str) -> SqliteDatabaseAdapter | ExternalAdapter:
        if db_id.startswith("ext:"):
            return await get_connection_store().adapter(db_id[len("ext:"):])
        return self._local(db_id, DatabaseCapability.BROWSE)

    async def list_databases(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for db_id, registered in database_registry().items():
            component = registered.component
            path = registered.path
            browsable = DatabaseCapability.BROWSE in component.capabilities
            exists = os.path.isfile(path) if browsable else None
            entry: dict[str, Any] = {
                "id": db_id, "name": component.name, "description": component.description,
                "path": path, "exists": exists, "external": False,
                "engine": component.engine, "volume_id": registered.volume_id,
                "managed_by": component.managed_by, "management_url": component.management_url,
                "capabilities": sorted(cap.value for cap in component.capabilities),
                "needs_restart": registered.needs_restart,
                "size_bytes": None, "measured_at": None, "table_count": None,
            }
            try:
                if exists:
                    entry["size_bytes"] = os.path.getsize(path)
                    entry["measured_at"] = time.time()
                if component.observe is not None:
                    observation = await component.observe(registered.root)
                    entry["size_bytes"] = observation.size_bytes
                    entry["measured_at"] = observation.measured_at
                    if not browsable and observation.size_bytes is not None:
                        entry["exists"] = observation.size_bytes > 0
                if exists:
                    entry["table_count"] = await self._local(db_id, DatabaseCapability.BROWSE).table_count(db_id)
            except Exception as exc:
                entry["error"] = str(exc)
            result.append(entry)
        try:
            for entry in await get_connection_store().list_source_entries():
                entry["capabilities"] = [DatabaseCapability.BROWSE.value, DatabaseCapability.QUERY.value]
                result.append(entry)
        except Exception as exc:
            log(f"外部连接清单获取失败: {exc}", "DEBUG", tag="数据库")
        return result

    async def list_tables(self, db_id: str, include_shadow: bool = False) -> list[dict[str, Any]]:
        adapter = await self._reader(db_id)
        if isinstance(adapter, ExternalAdapter):
            return await adapter.list_tables()
        return await adapter.list_tables(db_id, include_shadow)

    async def table_schema(self, db_id: str, table: str) -> dict[str, Any]:
        adapter = await self._reader(db_id)
        if isinstance(adapter, ExternalAdapter):
            return await adapter.table_schema(table)
        return await adapter.table_schema(db_id, table)

    async def browse_rows(
        self, db_id: str, table: str, *, page: int = 1, page_size: int = 50,
        sort: str | None = None, order: str = "asc", filter_col: str | None = None,
        filter_text: str | None = None,
    ) -> dict[str, Any]:
        adapter = await self._reader(db_id)
        if isinstance(adapter, ExternalAdapter):
            return await adapter.browse_rows(table, page=page, page_size=page_size, sort=sort,
                                            order=order, filter_col=filter_col, filter_text=filter_text)
        return await adapter.browse_rows(db_id, table, page=page, page_size=page_size, sort=sort,
                                        order=order, filter_col=filter_col, filter_text=filter_text)

    async def get_row(self, db_id: str, table: str, rowid: int) -> dict[str, Any]:
        return await self._local(db_id, DatabaseCapability.BROWSE).get_row(db_id, table, rowid)

    async def insert_row(self, db_id: str, table: str, values: dict[str, Any]) -> dict[str, Any]:
        self._local(db_id, DatabaseCapability.EDIT)
        return await self._editor.insert_row(db_id, table, values)

    async def update_row(self, db_id: str, table: str, rowid: int, values: dict[str, Any]) -> None:
        self._local(db_id, DatabaseCapability.EDIT)
        await self._editor.update_row(db_id, table, rowid, values)

    async def delete_row(self, db_id: str, table: str, rowid: int) -> None:
        self._local(db_id, DatabaseCapability.EDIT)
        await self._editor.delete_row(db_id, table, rowid)

    async def run_query(self, db_id: str, sql: str) -> dict[str, Any]:
        if db_id.startswith("ext:"):
            return await (await get_connection_store().adapter(db_id[len("ext:"):])).run_query(sql)
        return await self._local(db_id, DatabaseCapability.QUERY).run_query(db_id, sql)

    async def database_health(self, db_id: str) -> dict[str, Any]:
        return await self._local(db_id, DatabaseCapability.HEALTH).database_health(db_id)

    async def backup_database(self, db_id: str) -> dict[str, Any]:
        return await self._local(db_id, DatabaseCapability.BACKUP).backup_database(db_id)

    async def optimize_database(self, db_id: str, actions: list[str]) -> dict[str, Any]:
        return await self._local(db_id, DatabaseCapability.OPTIMIZE).optimize_database(db_id, actions)


_service: DatabaseService | None = None


def get_database_service() -> DatabaseService:
    """数据库管理连接的唯一持有者；路由和生命周期装配共享实例。"""
    global _service
    if _service is None:
        _service = DatabaseService()
    return _service


def register_lifecycle() -> None:
    """在 Web 服务之前登记，停止接单和排空请求后再关闭管理连接。"""
    from core.lifecycle import Lifecycle

    service = get_database_service()
    Lifecycle.register("database_management", service, cleanup=service.close_all, critical_cleanup=True)
    connections = get_connection_store()
    Lifecycle.register("database_connections", connections, cleanup=connections.close_all)
