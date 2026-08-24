"""MCP 工具 schema 解析：Tool 对象 → 名称/参数列表，注册名供应商合规整形。

参数 schema 保真：type 缺失的联合类型写法（anyOf/oneOf，MCP server
表达可选参数的惯用法）解引用取非 null 分支；default/items 等附加键
经 schema_extra 直通 wire schema——模型由此看到默认值与数组元素结构，
而不是被静默丢弃后按 string 兜底猜参数。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Tuple

from core.entity import ToolParam

# OpenAI function-calling 工具名上限：超限会导致供应商拒绝整个 tools 数组
_MAX_TOOL_NAME_LEN = 64


def _sanitize_tool_name(name: str) -> str:
    """注册名整形为供应商合法的函数名（[A-Za-z0-9_-]{1,64}）。

    非法字符（server 名含点号等）替换为下划线；超长截断并追加
    注册名 SHA-256 前 8 位十六进制防截断撞名。整形后名字变化时，
    原始名由调用方的 ``_tool_original_names`` 映射兜底还原。
    """
    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    if len(sanitized) > _MAX_TOOL_NAME_LEN:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        sanitized = f"{sanitized[:_MAX_TOOL_NAME_LEN - 9]}_{digest}"
    return sanitized


def _parse_mcp_tool(mcp_tool: Any) -> tuple[str, List[ToolParam]]:
    """解析 MCP Tool 对象为名称和参数列表。

    参数 schema 保真：type 缺失的联合类型写法（anyOf/oneOf，MCP server
    表达可选参数的惯用法）解引用取非 null 分支；default/items 等
    附加键经 schema_extra 直通 wire schema——模型由此看到默认值与
    数组元素结构，而不是被静默丢弃后按 string 兜底猜参数。
    """
    name = mcp_tool.name
    params: List[ToolParam] = []
    input_schema = getattr(mcp_tool, "inputSchema", None) or {}
    if isinstance(input_schema, dict):
        properties = input_schema.get("properties", {})
        required_list = input_schema.get("required", [])
        for p_name, p_schema in properties.items():
            if not isinstance(p_schema, dict):
                params.append(ToolParam(name=p_name, required=p_name in required_list))
                continue
            p_type, schema_extra = _parse_param_schema(p_schema)
            params.append(ToolParam(
                name=p_name,
                description=p_schema.get("description", ""),
                type=p_type,
                required=p_name in required_list,
                enum=p_schema.get("enum"),
                schema_extra=schema_extra or None,
            ))
    return name, params


def _parse_param_schema(p_schema: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """解析单个参数的 JSON Schema 片段为 (type, schema_extra)。

    - type 缺失而 anyOf/oneOf 存在 → 取首个非 null 分支的 type；
    - default/items/数值范围等附加键保留进 schema_extra（随 ToolParam
      直通 wire schema 的 properties 字段）。
    """
    extra: Dict[str, Any] = {}
    p_type = str(p_schema.get("type", "") or "")
    if not p_type:
        for union_key in ("anyOf", "oneOf"):
            union = p_schema.get(union_key)
            if isinstance(union, list):
                for branch in union:
                    if (
                        isinstance(branch, dict)
                        and branch.get("type")
                        and branch.get("type") != "null"
                    ):
                        p_type = str(branch["type"])
                        break
            if p_type:
                break
    if not p_type:
        p_type = "string"  # 保持既有兜底行为
    for key in ("default", "items", "minimum", "maximum", "pattern", "format"):
        if key in p_schema:
            extra[key] = p_schema[key]
    return p_type, extra
