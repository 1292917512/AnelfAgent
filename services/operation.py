"""操作服务门面 — Web 面到操作核心（agent/operation）与 MCP 工具目录的收口。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.operation import executor, framework
from services.mcp import MCPService

_mcp_service = MCPService()


class OperationService:
    """操作目录/执行/MCP 注册的服务面（Web 路由唯一交互对象）。"""

    def operations(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": s.id, "kind": s.kind, "title": s.title,
                "description": s.description, "annotation": s.annotation,
                "enabled": s.enabled, "server": s.server, "tool": s.tool,
                "params": s.params, "removable": s.removable,
            }
            for s in framework.list_operations()
        ]

    async def status(self) -> Dict[str, Any]:
        return await executor.status()

    def mcp_tools(self, server: str = "") -> List[Dict[str, Any]]:
        """已连接 server 的工具详情（注册面板选择用；server 空则全量）。"""
        connected = _mcp_service.get_connected_tools()
        targets = [server] if server else sorted(connected)
        details: List[Dict[str, Any]] = []
        for name in targets:
            if name not in connected:
                continue
            for tool in _mcp_service.get_server_tool_details(name):
                details.append({"server": name, **tool})
        return details

    def register_mcp(self, server: str, tool: str, note: str = "") -> Dict[str, Any]:
        connected = _mcp_service.get_connected_tools()
        if server not in connected:
            return {"ok": False, "error": f"server 未连接: {server}"}
        matched = next(
            (t for t in connected[server]
             if t == tool or t.rsplit("__", 1)[-1] == tool), "",
        )
        if not matched:
            return {"ok": False, "error": f"工具不存在: {tool}"}
        meta = next(
            (d for d in self.mcp_tools(server) if d["name"] == matched), {},
        )
        spec = framework.register_mcp_operation(
            server=server, tool=matched, annotation=note,
            description=str(meta.get("description") or ""),
            params=list(meta.get("params") or []),
        )
        return {"ok": True, "op_id": spec.id}

    def update(self, op_id: str, *, note: Optional[str], enabled: Optional[bool]) -> Dict[str, Any]:
        if framework.get_operation(op_id) is None:
            return {"ok": False, "error": f"操作不存在: {op_id}"}
        if note is not None:
            framework.set_annotation(op_id, note)
        if enabled is not None:
            framework.set_enabled(op_id, enabled)
        return {"ok": True}

    def remove(self, op_id: str) -> Dict[str, Any]:
        ok = framework.remove_operation(op_id)
        return {"ok": ok, "error": "" if ok else "操作不存在或不可删除"}

    async def execute(self, op_id: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Web 发起的操作执行（仅 MCP 操作——桌面动作需要屏幕坐标与上下文，
        AI 的 desktop_act 才是正确入口）。"""
        spec = framework.get_operation(op_id)
        if spec is None:
            return {"ok": False, "error": f"操作不存在: {op_id}"}
        if spec.kind != framework.KIND_MCP:
            return {"ok": False, "error": "桌面动作请在对话中由 AI 执行（desktop_act）"}
        return await executor.execute(op_id, args or {})
