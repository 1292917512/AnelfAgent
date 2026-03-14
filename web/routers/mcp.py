"""MCP 服务管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import MCPService

router = APIRouter(prefix="/mcp", tags=["mcp"])

_mcp_svc = MCPService()


@router.get("/")
async def list_servers() -> List[Dict[str, Any]]:
    return _mcp_svc.list_servers()


@router.get("/config")
async def get_config() -> Dict[str, str]:
    return {"content": _mcp_svc.get_config_json()}


class SaveConfigRequest(BaseModel):
    content: str


@router.put("/config")
async def save_config(req: SaveConfigRequest) -> Dict[str, str]:
    try:
        _mcp_svc.save_config_json(req.content)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(400, str(e))


class AddServerRequest(BaseModel):
    name: str
    url: str


@router.post("/")
async def add_server(req: AddServerRequest) -> Dict[str, str]:
    _mcp_svc.add_server(req.name, req.url)
    return {"status": "ok"}


@router.delete("/{name}")
async def remove_server(name: str) -> Dict[str, str]:
    _mcp_svc.remove_server(name)
    return {"status": "ok"}


@router.put("/{name}/toggle")
async def toggle_server(name: str) -> Dict[str, Any]:
    import asyncio
    # asyncio.shield 防止 HTTP 连接中断时 CancelledError 污染底层 MCP 操作
    try:
        return await asyncio.shield(asyncio.to_thread(_mcp_svc.toggle_server, name))
    except asyncio.CancelledError:
        raise HTTPException(503, "操作被中断，请重试")


@router.get("/{name}/tools")
async def get_server_tools(name: str) -> List[str]:
    return _mcp_svc.get_server_tools(name)
