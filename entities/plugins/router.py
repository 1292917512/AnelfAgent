"""插件管理实体的 HTTP 路由（自动挂载到 /api/entity/plugins）。

插件与市场管理面：已装插件列表/详情/安装/移除/升级/启停，
市场订阅列表/添加/移除/刷新，跨市场检索。
"""

from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.plugins import PluginManager, get_plugin_manager
from core.plugins.manifest import PluginError, PluginNotFound
from core.plugins.status import get_operation_board
from entities.plugins.activation import wire_plugin_manager
from entities.plugins.diagnostics import plugin_detail


class InstallIn(BaseModel):
    """市场安装请求。"""

    name: str
    marketplace: str = ""
    expected_revision: str = ""


class InstallSourceIn(BaseModel):
    """直接来源安装请求。"""

    source: str
    ref: str = ""
    subdir: str = ""
    expected_revision: str = ""


class InspectIn(BaseModel):
    name: str = ""
    marketplace: str = ""
    source: str = ""
    ref: str = ""
    subdir: str = ""


class ToggleIn(BaseModel):
    """启停请求。"""

    enabled: bool


class MarketplaceIn(BaseModel):
    """市场订阅请求。"""

    name: str
    source: str
    ref: str = ""


def _manager() -> PluginManager:
    manager = get_plugin_manager()
    wire_plugin_manager(manager)
    return manager


def _http_error(exc: PluginError) -> HTTPException:
    return HTTPException(status_code=404 if isinstance(exc, PluginNotFound) else 400, detail=str(exc))


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("")
    async def list_plugins() -> List[dict]:
        """列出全部已安装插件。"""
        return [p.to_dict() for p in _manager().list_plugins()]

    @router.get("/search")
    async def search_plugins(q: str = "", marketplace: str = "", offset: int = 0, limit: int | None = None) -> dict:
        """跨市场检索可安装插件。"""
        try:
            return await asyncio.to_thread(_manager().search_page, q, marketplace, offset, limit)
        except PluginError as e:
            raise _http_error(e) from e

    @router.get("/marketplaces")
    async def list_marketplaces() -> List[dict]:
        """列出已订阅市场。"""
        return await asyncio.to_thread(_manager().list_marketplaces)

    @router.post("/marketplaces", status_code=201)
    async def add_marketplace(body: MarketplaceIn) -> dict:
        """订阅 GitHub Topic、Git 仓库或本地市场目录。"""
        try:
            record = await asyncio.to_thread(
                _manager().add_marketplace, body.name, body.source, body.ref,
            )
            return record.to_dict()
        except PluginError as e:
            raise _http_error(e) from e

    @router.delete("/marketplaces/{name}")
    async def remove_marketplace(name: str) -> dict:
        """取消订阅市场。"""
        try:
            record = await asyncio.to_thread(_manager().remove_marketplace, name)
            return {"removed": True, "name": record.name}
        except PluginError as e:
            raise _http_error(e) from e

    @router.post("/marketplaces/{name}/toggle")
    async def toggle_marketplace(name: str, body: ToggleIn) -> dict:
        """保存来源开关，保留本地目录快照。"""
        try:
            record = await asyncio.to_thread(_manager().toggle_marketplace, name, body.enabled)
            return {"name": record.name, "enabled": record.enabled}
        except PluginError as e:
            raise _http_error(e) from e

    @router.post("/marketplaces/refresh")
    async def refresh_marketplaces(name: str = "") -> dict:
        """刷新市场目录（git 市场执行 pull）。"""
        try:
            errors: dict[str, str] = {}
            results = await asyncio.to_thread(_manager().refresh_marketplaces, name, errors=errors)
            return {"results": results, "errors": errors}
        except PluginError as e:
            raise _http_error(e) from e

    @router.post("/inspect")
    async def inspect_plugin(body: InspectIn) -> dict:
        try:
            return await asyncio.to_thread(_manager().inspect, **body.model_dump())
        except PluginError as exc:
            raise _http_error(exc) from exc

    @router.get("/diagnostics/operations")
    async def operations(operation_id: str = "") -> list[dict]:
        return get_operation_board().operations(operation_id)

    @router.get("/{name}")
    async def plugin_info(name: str) -> dict:
        """插件详情。"""
        record = _manager().get_plugin(name)
        if record is None:
            raise HTTPException(status_code=404, detail=f"插件 '{name}' 未安装")
        return plugin_detail(record)

    @router.post("", status_code=201)
    async def install_plugin(body: InstallIn) -> dict:
        """从已订阅市场安装插件。"""
        try:
            record, operation_id = await asyncio.to_thread(_manager().install_package, **body.model_dump())
            return {**record.to_dict(), "operation_id": operation_id}
        except PluginError as e:
            raise _http_error(e) from e

    @router.post("/from-source", status_code=201)
    async def install_from_source(body: InstallSourceIn) -> dict:
        """直接从 git URL 或本地路径安装插件。"""
        try:
            record, operation_id = await asyncio.to_thread(_manager().install_package, **body.model_dump())
            return {**record.to_dict(), "operation_id": operation_id}
        except PluginError as e:
            raise _http_error(e) from e

    @router.post("/{name}/upgrade")
    async def upgrade_plugin(name: str) -> dict:
        """升级单个插件。"""
        try:
            record, changed = await asyncio.to_thread(_manager().upgrade, name)
            return {"name": record.name, "upgraded": changed, "version": record.version}
        except PluginError as e:
            raise _http_error(e) from e

    @router.post("/upgrade-all")
    async def upgrade_all() -> dict:
        """升级全部已安装插件。"""
        errors: dict[str, str] = {}
        results = await asyncio.to_thread(_manager().upgrade_all, errors=errors)
        return {"results": results, "errors": errors}

    @router.post("/{name}/toggle")
    async def toggle_plugin(name: str, body: ToggleIn) -> dict:
        """启用/禁用插件。"""
        try:
            record = await asyncio.to_thread(_manager().toggle, name, body.enabled)
            return {"name": record.name, "enabled": record.enabled}
        except PluginError as e:
            raise _http_error(e) from e

    @router.delete("/{name}")
    async def remove_plugin(name: str) -> dict:
        """移除插件。"""
        try:
            record = await asyncio.to_thread(_manager().remove, name)
            return {"removed": True, "name": record.name}
        except PluginError as e:
            raise _http_error(e) from e

    return router
