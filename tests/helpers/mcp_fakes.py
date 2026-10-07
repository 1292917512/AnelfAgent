"""MCP SDK 真实模型构造器：测试与被测代码共用同一份 SDK 字段定义。

构造一律经 ``model_validate`` 走 wire 报文形态（camelCase 别名），与
服务端上线 payload 的解析路径完全一致——SDK 字段改名时测试随被测
代码一起失败，不会出现 SimpleNamespace mock 字段名漂移导致的
"测试全绿、生产全灭"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mcp.types import CallToolResult, ListToolsResult, Tool


def make_tool(
    name: str,
    description: str = "",
    input_schema: Optional[Dict[str, Any]] = None,
    read_only: bool = False,
) -> Tool:
    """以 wire 报文形态构造真实 ``mcp.types.Tool``。"""
    payload: Dict[str, Any] = {
        "name": name,
        "description": description,
        "inputSchema": input_schema or {"type": "object", "properties": {}},
    }
    if read_only:
        payload["annotations"] = {"readOnlyHint": True}
    return Tool.model_validate(payload)


def make_result(
    *blocks: Any,
    structured: Optional[Dict[str, Any]] = None,
    is_error: bool = False,
) -> CallToolResult:
    """以 wire 报文形态构造真实 ``mcp.types.CallToolResult``（内容为 SDK 内容块）。"""
    payload: Dict[str, Any] = {"content": list(blocks), "isError": is_error}
    if structured is not None:
        payload["structuredContent"] = structured
    return CallToolResult.model_validate(payload)


def make_tool_list(tools: List[Tool]) -> ListToolsResult:
    """伪造 ``ClientSession.list_tools`` 的返回（嵌套 Tool 模型直传）。"""
    return ListToolsResult(tools=list(tools))
