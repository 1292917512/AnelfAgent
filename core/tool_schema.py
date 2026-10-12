"""
工具参数 Schema 提取（签名 → ToolParam 列表）。

模型看到与 Python 类型一致的参数结构及约束；嵌套结构按声明增加必要的
schema token，减少错误重试；注册时生成，运行时不改写稳定提示词。

从函数/绑定方法签名与 docstring 自动推导 LLM 工具参数：
- 跳过 self/cls 与 *args/**kwargs
- 支持 `from __future__ import annotations` 产生的字符串注解
- 参数描述取自 Google style（Args:）或 Sphinx style（:param x:）docstring
- 支持 ``func._schema_extra`` 注入额外 JSON Schema 字段（如 items/minItems）
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, List, get_type_hints

from pydantic import TypeAdapter

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


def extract_tool_params(func: Callable) -> List[ToolParam]:
    """从函数签名和 docstring 提取参数列表（含描述）。

    若函数设置了 ``_schema_extra`` 属性（dict[param_name, dict]），
    对应参数的额外 JSON Schema 字段（如 items / minItems）会合并到 ToolParam。
    """
    sig = inspect.signature(func)
    annotations = get_type_hints(func, include_extras=True)
    doc_params = parse_docstring_args(func.__doc__ or "")
    extras: Dict[str, dict] = getattr(func, "_schema_extra", {})
    params: List[ToolParam] = []
    for p_name, p in sig.parameters.items():
        if p_name in ("self", "cls"):
            continue
        # *args / **kwargs 是容错性捕获参数，不应出现在 LLM schema 中
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = annotations.get(p_name, str)
        adapter: TypeAdapter[Any] = TypeAdapter(annotation)
        schema = adapter.json_schema(ref_template=f"#/$defs/{p_name}_{{model}}")
        definitions = schema.get("$defs", {})
        if definitions:
            schema["$defs"] = {f"{p_name}_{key}": value for key, value in definitions.items()}
        schema.pop("title", None)
        schema.update(extras.get(p_name, {}))
        branches = [branch for branch in schema.get("anyOf", []) if branch.get("type") != "null"]
        json_type = schema.get("type", branches[0].get("type", "") if len(branches) == 1 else "")
        required = p.default is inspect.Parameter.empty
        params.append(
            ToolParam(
                name=p_name,
                description=doc_params.get(p_name, ""),
                type=json_type,
                required=required,
                default=p.default,
                schema_extra=schema,
                adapter=adapter,
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


def coerce_bool_arg(value: Any, default: bool) -> bool:
    """将工具参数稳健转为 bool（兼容 LLM 误传字符串）。

    无法识别的字符串返回调用方提供的默认值。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default
    return bool(value)
