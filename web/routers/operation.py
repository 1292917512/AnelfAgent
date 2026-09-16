"""操作管理 API 路由 — 操作目录 / 注释与启停 / MCP 注册 / 执行与运行态。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from services.operation import OperationService

router = APIRouter(prefix="/operation", tags=["operation"])

_op_svc = OperationService()


@router.get("/list")
async def list_operations() -> Dict[str, Any]:
    return {"items": _op_svc.operations()}


@router.get("/status")
async def operation_status() -> Dict[str, Any]:
    return await _op_svc.status()


@router.get("/mcp-tools")
async def mcp_tools(server: str = Query(default="")) -> Dict[str, Any]:
    """已连接 server 的工具详情（注册面板选择用）。"""
    return {"items": _op_svc.mcp_tools(server)}


class RegisterMcpRequest(BaseModel):
    server: str
    tool: str
    note: str = ""


@router.post("/register-mcp")
async def register_mcp(req: RegisterMcpRequest) -> Dict[str, Any]:
    return _op_svc.register_mcp(req.server, req.tool, req.note)


class UpdateOperationRequest(BaseModel):
    note: Optional[str] = None
    enabled: Optional[bool] = None


@router.put("/{op_id}")
async def update_operation(op_id: str, req: UpdateOperationRequest) -> Dict[str, Any]:
    return _op_svc.update(op_id, note=req.note, enabled=req.enabled)


@router.delete("/{op_id}")
async def remove_operation(op_id: str) -> Dict[str, Any]:
    return _op_svc.remove(op_id)


class ExecuteRequest(BaseModel):
    args: Dict[str, Any] = {}


@router.post("/{op_id}/execute")
async def execute_operation(op_id: str, req: ExecuteRequest) -> Dict[str, Any]:
    return await _op_svc.execute(op_id, req.args)
