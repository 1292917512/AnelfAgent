"""插件管理实体的 HTTP 路由（自动挂载到 /api/entity/plugins）。

插件与市场管理面：已装插件列表/详情/安装/移除/升级/启停，
市场订阅列表/添加/移除/刷新，跨市场检索。
"""

from __future__ import annotations

import asyncio
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.plugins import get_plugin_manager
from core.plugins.manifest import PluginError
from entities.plugins.activation import wire_plugin_manager


class InstallIn(BaseModel):
    """市场安装请求。"""

    name: str
    marketplace: str = ""


class InstallSourceIn(BaseModel):
    """直接来源安装请求。"""

    source: str
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


def _manager():
    manager = get_plugin_manager()
    wire_plugin_manager(manager)
    return manager


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("")
    async def list_plugins() -> List[dict]:
        """列出全部已安装插件。"""
        return [p.to_dict() for p in _manager().list_plugins()]

    @router.get("/search")
    async def search_plugins(q: str = "") -> dict:
        """跨市场检索可安装插件。"""
        results = await asyncio.to_thread(_manager().search, q)
        return {"count": len(results), "plugins": results}

    @router.get("/marketplaces")
    async def list_marketplaces() -> List[dict]:
        """列出已订阅市场。"""
        return await asyncio.to_thread(_manager().list_marketplaces)

    @router.post("/marketplaces", status_code=201)
    async def add_marketplace(body: MarketplaceIn) -> dict:
        """订阅市场（git URL 或本地目录）。"""
        try:
            record = await asyncio.to_thread(
                _manager().add_marketplace, body.name, body.source, body.ref,
            )
            return record.to_dict()
        except PluginError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.delete("/marketplaces/{name}")
    async def remove_marketplace(name: str) -> dict:
        """取消订阅市场。"""
        try:
            record = await asyncio.to_thread(_manager().remove_marketplace, name)
            return {"removed": True, "name": record.name}
        except PluginError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.post("/marketplaces/refresh")
    async def refresh_marketplaces(name: str = "") -> dict:
        """刷新市场目录（git 市场执行 pull）。"""
        try:
            results = await asyncio.to_thread(_manager().refresh_marketplaces, name)
            return {"results": results}
        except PluginError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @router.get("/{name}")
    async def plugin_info(name: str) -> dict:
        """插件详情。"""
        record = _manager().get_plugin(name)
        if record is None:
            raise HTTPException(status_code=404, detail=f"插件 '{name}' 未安装")
        return record.to_dict()

    @router.post("", status_code=201)
    async def install_plugin(body: InstallIn) -> dict:
        """从已订阅市场安装插件。"""
        try:
            record = await asyncio.to_thread(_manager().install, body.name, body.marketplace)
            return record.to_dict()
        except PluginError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.post("/from-source", status_code=201)
    async def install_from_source(body: InstallSourceIn) -> dict:
        """直接从 git URL 或本地路径安装插件。"""
        try:
            record = await asyncio.to_thread(
                _manager().install_from_source, body.source, body.ref, body.subdir,
            )
            return record.to_dict()
        except PluginError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.post("/{name}/upgrade")
    async def upgrade_plugin(name: str) -> dict:
        """升级单个插件。"""
        try:
            record, changed = await asyncio.to_thread(_manager().upgrade, name)
            return {"name": record.name, "upgraded": changed, "version": record.version}
        except PluginError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @router.post("/upgrade-all")
    async def upgrade_all() -> dict:
        """升级全部已安装插件。"""
        results = await asyncio.to_thread(_manager().upgrade_all)
        return {"results": results}

    @router.post("/{name}/toggle")
    async def toggle_plugin(name: str, body: ToggleIn) -> dict:
        """启用/禁用插件。"""
        try:
            record = await asyncio.to_thread(_manager().toggle, name, body.enabled)
            return {"name": record.name, "enabled": record.enabled}
        except PluginError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @router.delete("/{name}")
    async def remove_plugin(name: str) -> dict:
        """移除插件。"""
        try:
            record = await asyncio.to_thread(_manager().remove, name)
            return {"removed": True, "name": record.name}
        except PluginError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    return router
