"""
工具参数 Schema 提取（签名 → ToolParam 列表）。

从函数/绑定方法签名与 docstring 自动推导 LLM 工具参数：
- 跳过 self/cls 与 *args/**kwargs
- 支持 `from __future__ import annotations` 产生的字符串注解
- 参数描述取自 Google style（Args:）或 Sphinx style（:param x:）docstring
- 支持 ``func._schema_extra`` 注入额外 JSON Schema 字段（如 items/minItems）
"""

from __future__ import annotations

import inspect
from typing import Callable, Dict, List

from core.entity import ToolParam


def get_first_line(docstring: str | None) -> str:
    """提取 docstring 首行作为工具描述。"""
    if not docstring:
        return ""
    for line in docstring.strip().split("\n"):
        line = line.strip()
        if line:
            return line
    return ""


_PY_ANNOTATION_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

# `from __future__ import annotations` 使注解懒加载为字符串，需额外映射
_STR_ANNOTATION_MAP = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "List": "array",
    "Dict": "object",
    "Optional[str]": "string",
    "Optional[int]": "integer",
    "Optional[float]": "number",
    "Optional[bool]": "boolean",
}


def extract_tool_params(func: Callable) -> List[ToolParam]:
    """从函数签名和 docstring 提取参数列表（含描述）。

    若函数设置了 ``_schema_extra`` 属性（dict[param_name, dict]），
    对应参数的额外 JSON Schema 字段（如 items / minItems）会合并到 ToolParam。
    """
    sig = inspect.signature(func)
    doc_params = parse_docstring_args(func.__doc__ or "")
    extras: Dict[str, dict] = getattr(func, "_schema_extra", {})
    params: List[ToolParam] = []
    for p_name, p in sig.parameters.items():
        if p_name in ("self", "cls"):
            continue
        # *args / **kwargs 是容错性捕获参数，不应出现在 LLM schema 中
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = p.annotation
        if annotation == inspect.Parameter.empty:
            json_type = "string"
        elif isinstance(annotation, str):
            json_type = _STR_ANNOTATION_MAP.get(annotation, "string")
        else:
            json_type = _PY_ANNOTATION_MAP.get(annotation, "string")
        required = p.default is inspect.Parameter.empty
        params.append(
            ToolParam(
                name=p_name,
                description=doc_params.get(p_name, ""),
                type=json_type,
                required=required,
                default=p.default,
                schema_extra=extras.get(p_name),
            )
        )
    return params


def parse_docstring_args(docstring: str) -> Dict[str, str]:
    """从 docstring 提取参数描述（支持 Google style / Sphinx style）。"""
    result: Dict[str, str] = {}
    if not docstring:
        return result

    lines = docstring.split("\n")
    in_args = False

    for line in lines:
        stripped = line.strip()

        if stripped.lower() in ("args:", "arguments:", "parameters:"):
            in_args = True
            continue

        if in_args and stripped and not stripped.startswith("-") and ":" not in stripped:
            if not line.startswith((" ", "\t")):
                in_args = False
                continue

        if in_args and ":" in stripped:
            key, _, desc = stripped.partition(":")
            key = key.strip().lstrip("-").strip()
            if key and not key.startswith("*"):
                result[key] = desc.strip()

        if stripped.startswith(":param "):
            rest = stripped[7:]
            key, _, desc = rest.partition(":")
            key = key.strip()
            if key:
                result[key] = desc.strip()

    return result
