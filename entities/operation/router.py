"""操作实体的 HTTP 路由（自动挂载到 /api/entity/operation）。

经 web/server.py 的 _mount_entity_routers 扫描发现：
- 操作目录 / 注释与启停 / MCP 关联注册 / 移除
- 已连接 server 工具详情（注册面板选择用）
- 测试执行（仅 MCP 关联）与运行态
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from .service import OperationService


def build_router() -> APIRouter:
    router = APIRouter()
    svc = OperationService()

    @router.get("/list")
    async def list_operations() -> Dict[str, Any]:
        return {"items": svc.operations()}

    @router.get("/status")
    async def operation_status() -> Dict[str, Any]:
        return await svc.status()

    @router.get("/mcp-tools")
    async def mcp_tools(server: str = Query(default="")) -> Dict[str, Any]:
        """已连接 server 的工具详情（注册面板选择用）。"""
        return {"items": svc.mcp_tools(server)}

    @router.post("/register-mcp")
    async def register_mcp(req: RegisterMcpRequest) -> Dict[str, Any]:
        return svc.register_mcp(req.server, req.tool, req.note)

    @router.put("/{op_id}")
    async def update_operation(op_id: str, req: UpdateOperationRequest) -> Dict[str, Any]:
        return svc.update(op_id, note=req.note, enabled=req.enabled)

    @router.delete("/{op_id}")
    async def remove_operation(op_id: str) -> Dict[str, Any]:
        return svc.remove(op_id)

    @router.post("/{op_id}/execute")
    async def execute_operation(op_id: str, req: ExecuteRequest) -> Dict[str, Any]:
        return await svc.execute(op_id, req.args)

    return router


class RegisterMcpRequest(BaseModel):
    server: str
    tool: str
    note: str = ""


class UpdateOperationRequest(BaseModel):
    note: Optional[str] = None
    enabled: Optional[bool] = None


class ExecuteRequest(BaseModel):
    args: Dict[str, Any] = {}
