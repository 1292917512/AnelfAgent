"""
实体开发标准接口。

所有实体模块统一使用本模块提供的装饰器进行声明与注册，
注册目标为 ``core.entity.EntityRegistry``，不依赖 ``agent``。

两种注册模式：

1. 立即注册 — 模块导入时自动注册（适合无运行时依赖的 entities/ 层工具）::

    from entities._sdk import tool, entity

    entity("weather", "天气查询服务")

    @tool(name="get_weather", group="weather")
    async def get_weather(city: str) -> str:
        ...

2. 延迟注册 — 装饰时仅收集元数据，运行时注入依赖后调用 activate_group 批量注册::

    from entities._sdk import deferred_tool, activate_group

    @deferred_tool(group="memory", tags=["always"], source="mind.memory")
    async def memorize(content: str) -> str:
        ...

    def register_memory_tools(store, embedder):
        global _store, _embedder
        _store, _embedder = store, embedder
        activate_group("memory", "长期记忆 - 记忆存储、语义检索")
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, List, Optional, TypeVar

from core.entity import EntityRegistry, ToolParam

F = TypeVar("F", bound=Callable[..., Any])


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    group: str = "default",
    tags: Optional[List[str]] = None,
    cacheable: bool = False,
    timeout: Optional[float] = None,
) -> Callable[[F], F]:
    """装饰器：将函数注册为 LLM 可调用工具（注册到 EntityRegistry）。

    参数的名称、类型、是否必填从函数签名自动推导。
    
    Args:
        timeout: 工具执行超时时间（秒），默认使用全局配置（30秒）
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or _get_first_line(func.__doc__) or tool_name
        params = _extract_params(func)

        meta = {}
        if timeout is not None:
            meta["timeout"] = timeout

        EntityRegistry.register_tool(
            name=tool_name,
            func=func,
            description=tool_desc,
            group=group,
            params=params,
            tags=tags or [],
            source="internal",
            meta=meta,
        )
        return func

    return decorator


def entity(group: str, description: str) -> None:
    """声明实体分组及其描述（立即注册），AI 将自动发现该实体。"""
    EntityRegistry.register_group(group, description)


# ------------------------------------------------------------------
# 延迟注册（适合需要运行时依赖注入的 core 层工具）
# ------------------------------------------------------------------

_deferred_registry: dict[str, list[dict]] = {}


def deferred_tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    group: str = "default",
    tags: Optional[List[str]] = None,
    source: str = "internal",
    timeout: Optional[float] = None,
) -> Callable[[F], F]:
    """延迟注册装饰器：装饰时仅收集元数据，activate_group() 时批量注册。

    用于需要运行时依赖注入的工具（如 MemoryStore、Embedder 等）。
    参数名称、类型、描述从函数签名和 docstring 自动推导。
    
    Args:
        timeout: 工具执行超时时间（秒），默认使用全局配置（30秒）
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or _get_first_line(func.__doc__) or tool_name
        params = _extract_params(func)
        
        meta = {}
        if timeout is not None:
            meta["timeout"] = timeout
        
        _deferred_registry.setdefault(group, []).append({
            "name": tool_name, "func": func, "description": tool_desc,
            "group": group, "params": params, "tags": tags or [], 
            "source": source, "meta": meta,
        })
        return func
    return decorator


def activate_group(group: str, description: str = "") -> int:
    """将延迟注册的工具批量注册到 EntityRegistry，返回注册数量。

    通常在 register_xxx_tools() 中注入依赖后调用。
    """
    entries = _deferred_registry.pop(group, [])
    if not entries:
        return 0
    if description:
        EntityRegistry.register_group(group, description)
    for e in entries:
        EntityRegistry.register_tool(**e)
    return len(entries)


# ------------------------------------------------------------------
# LLM 桥接（延迟导入 agent.llm，供 entities 层使用）
# ------------------------------------------------------------------


def get_llm_manager() -> Any:
    """获取 LLMManager 实例（延迟导入 agent.llm）。"""
    from agent.llm import get_llm_manager as _get
    return _get()


def load_image_from_path(path: str) -> Any:
    """从本地路径加载图片为 base64 ImageContent。"""
    from agent.llm.image_utils import load_image_from_path as _load
    return _load(path)


def download_image_to_base64(url: str) -> Any:
    """下载 URL 图片并转为 base64 ImageContent。"""
    from agent.llm.image_utils import download_image_to_base64 as _dl
    return _dl(url)


def get_image_content_class() -> type:
    """获取 ImageContent 类型。"""
    from agent.llm.types import ImageContent
    return ImageContent


def get_model_type_enum() -> Any:
    """获取 ModelType 枚举。"""
    from agent.llm.llm_manager import ModelType
    return ModelType


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_first_line(docstring: str | None) -> str:
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


def _extract_params(func: Callable) -> List[ToolParam]:
    """从函数签名和 docstring 提取参数列表（含描述）。"""
    sig = inspect.signature(func)
    doc_params = _parse_docstring_args(func.__doc__ or "")
    params: List[ToolParam] = []
    for p_name, p in sig.parameters.items():
        if p_name in ("self", "cls"):
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
            )
        )
    return params


def _parse_docstring_args(docstring: str) -> dict[str, str]:
    """从 docstring 提取参数描述（支持 Google style / Sphinx style）。"""
    result: dict[str, str] = {}
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
