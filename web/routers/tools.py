"""工具管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import ToolService

router = APIRouter(prefix="/tools", tags=["tools"])

_tool_svc = ToolService()


class ToolMetaUpdate(BaseModel):
    tags: Optional[List[str]] = None
    description: Optional[str] = None


@router.get("/")
async def list_tools() -> List[Dict[str, Any]]:
    return _tool_svc.list_tools()


@router.get("/grouped")
async def list_grouped_tools() -> List[Dict[str, Any]]:
    return _tool_svc.list_grouped_tools()


@router.put("/{name}/toggle")
async def toggle_tool(name: str) -> Dict[str, Any]:
    try:
        enabled = _tool_svc.toggle_tool(name)
        return {"name": name, "enabled": enabled}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/{name}/meta")
async def update_tool_meta(name: str, body: ToolMetaUpdate) -> Dict[str, Any]:
    """修改工具的 tags 和 description（持久化到 app_config.json）。"""
    try:
        ok = _tool_svc.update_tool_meta(name, tags=body.tags, description=body.description)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(404, f"工具 '{name}' 不存在")
    return {"status": "ok", "name": name, "tags": body.tags, "description": body.description}


@router.put("/group/{group}/toggle")
async def toggle_group(group: str) -> Dict[str, Any]:
    enabled = _tool_svc.toggle_group(group)
    return {"group": group, "enabled": enabled}


@router.post("/reload")
async def reload_entities() -> Dict[str, Any]:
    result = _tool_svc.reload_entities()
    return result


@router.get("/plugins")
async def list_plugins() -> List[Dict[str, Any]]:
    return _tool_svc.list_plugins()
