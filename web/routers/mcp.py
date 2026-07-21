"""MCP 服务管理 API 路由。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import MCPService

router = APIRouter(prefix="/mcp", tags=["mcp"])

_mcp_svc = MCPService()


class ServerConfigPayload(BaseModel):
    """MCP server 完整配置字段（创建/编辑共用，缺省字段不提交）。"""

    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    headers: Optional[Dict[str, str]] = None
    transport: Optional[str] = None
    enabled: Optional[bool] = None
    timeout: Optional[float] = None
    sse_read_timeout: Optional[float] = None
    call_timeout: Optional[float] = None


class CreateServerRequest(ServerConfigPayload):
    name: str


class SaveConfigRequest(BaseModel):
    content: str


@router.get("/")
async def list_servers() -> List[Dict[str, Any]]:
    return _mcp_svc.list_servers()


@router.get("/config")
async def get_config() -> Dict[str, str]:
    return {"content": _mcp_svc.get_config_json()}


@router.put("/config")
async def save_config(req: SaveConfigRequest) -> Dict[str, str]:
    try:
        await asyncio.to_thread(_mcp_svc.save_config_json, req.content)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/")
async def add_server(req: CreateServerRequest) -> Dict[str, Any]:
    name = req.name.strip()
    if not name:
        raise HTTPException(400, "服务器名称不能为空")
    patch = req.model_dump(exclude={"name"}, exclude_none=True)
    try:
        return await asyncio.to_thread(_mcp_svc.create_server, name, patch)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/{name}")
async def get_server(name: str) -> Dict[str, Any]:
    cfg = _mcp_svc.get_server_config(name)
    if cfg is None:
        raise HTTPException(404, f"服务器 '{name}' 不存在")
    return cfg


@router.put("/{name}")
async def update_server(name: str, req: ServerConfigPayload) -> Dict[str, Any]:
    patch = req.model_dump(exclude_none=True)
    try:
        return await asyncio.to_thread(
            _mcp_svc.update_server_config, name, patch, replace=True,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{name}")
async def remove_server(name: str) -> Dict[str, str]:
    try:
        await asyncio.to_thread(_mcp_svc.remove_server, name)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/{name}/toggle")
async def toggle_server(name: str) -> Dict[str, Any]:
    # asyncio.shield 防止 HTTP 连接中断时 CancelledError 污染底层 MCP 操作
    try:
        return await asyncio.shield(asyncio.to_thread(_mcp_svc.toggle_server, name))
    except asyncio.CancelledError:
        raise HTTPException(503, "操作被中断，请重试")


@router.get("/{name}/tools")
async def get_server_tools(name: str) -> List[Dict[str, Any]]:
    return _mcp_svc.get_server_tool_details(name)
