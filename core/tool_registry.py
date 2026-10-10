"""工具声明与延迟激活；注册元数据由核心持有，业务模块只提供实现。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from core.entity import EntityRegistry
from core.tool_schema import extract_tool_params, get_first_line

F = TypeVar("F", bound=Callable[..., Any])


def tool(
    name: Optional[str] = None,
    description: Optional[str] = None,
    group: str = "default",
    tags: Optional[List[str]] = None,
    cacheable: bool = False,
    timeout: Optional[float] = None,
    check_fn: Optional[Callable[[], Any]] = None,
    allow_sleep: bool = False,
    sleep_brief: str = "",
    concurrency_safe: bool = False,
    risk: str = "",
    path_resolver: Optional[Callable[[str], Path]] = None,
) -> Callable[[F], F]:
    """装饰器：将函数注册为 LLM 可调用工具（注册到 EntityRegistry）。

    参数的名称、类型、是否必填从函数签名自动推导。

    Args:
        timeout: 工具执行超时时间（秒），默认使用全局默认（60秒）
        check_fn: 工具门控前置检查（返回 bool 或 Awaitable[bool]），
            检查不通过时工具不出现在 LLM schema 中
        allow_sleep: 是否允许沉睡（沉睡时仅展示 sleep_brief）
        sleep_brief: 沉睡状态下展示给 AI 的简短描述
        concurrency_safe: 是否可与其他安全工具并行执行（只读工具才应开启，
            默认 False — fail-closed 语义）。这是对整条执行链的断言：
            框架层（事件/审批/ContextVar 隔离）已保证并发安全，
            标注者只需确保工具体自身只读无共享写状态
        risk: 风险等级标记（如 CRITICAL），供规则与自动评审判断执行条件
        path_resolver: 将工具文件参数解析为绝对路径，供界面定位；不改变调用参数或授权
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or get_first_line(func.__doc__) or tool_name
        params = extract_tool_params(func)

        meta: Dict[str, Any] = {}
        if timeout is not None:
            meta["timeout"] = timeout
        if concurrency_safe:
            meta["concurrency_safe"] = True
        if risk:
            meta["risk"] = risk
        if path_resolver is not None:
            meta["path_resolver"] = path_resolver

        EntityRegistry.register_tool(
            name=tool_name,
            func=func,
            description=tool_desc,
            group=group,
            params=params,
            tags=tags or [],
            source="internal",
            meta=meta,
            check_fn=check_fn,
            allow_sleep=allow_sleep,
            sleep_brief=sleep_brief,
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
    check_fn: Optional[Callable[[], Any]] = None,
    allow_sleep: bool = False,
    sleep_brief: str = "",
    concurrency_safe: bool = False,
    risk: str = "",
    schema_extra: Optional[Dict[str, Dict[str, Any]]] = None,
    path_resolver: Optional[Callable[[str], Path]] = None,
) -> Callable[[F], F]:
    """延迟注册装饰器：装饰时仅收集元数据，activate_group() 时批量注册。

    用于需要运行时依赖注入的工具（如 MemoryStore、Embedder 等）。
    参数名称、类型、描述从函数签名和 docstring 自动推导。

    Args:
        timeout: 工具执行超时时间（秒），默认使用全局默认（60秒）
        check_fn: 工具门控前置检查（返回 bool 或 Awaitable[bool]）
        allow_sleep: 是否允许沉睡（沉睡时仅展示 sleep_brief）
        sleep_brief: 沉睡状态下展示给 AI 的简短描述
        concurrency_safe: 是否可与其他安全工具并行执行（只读工具才应开启；
            框架层已保证并发安全，标注者只需确保工具体自身只读无共享写状态）
        risk: 风险等级标记（如 CRITICAL），供规则与自动评审判断执行条件
        path_resolver: 将工具文件参数解析为绝对路径，供界面定位；不改变调用参数或授权
        schema_extra: 参数级额外 JSON Schema 字段（{参数名: {...}}，如
            items/minItems），签名推导只到顶层类型，复杂数组/对象参数
            的完整 wire schema 经此声明
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or get_first_line(func.__doc__) or tool_name
        params = extract_tool_params(func)
        if schema_extra:
            for p in params:
                extra = schema_extra.get(p.name)
                if extra:
                    p.schema_extra = {**(p.schema_extra or {}), **extra}

        meta: Dict[str, Any] = {}
        if timeout is not None:
            meta["timeout"] = timeout
        if concurrency_safe:
            meta["concurrency_safe"] = True
        if risk:
            meta["risk"] = risk
        if path_resolver is not None:
            meta["path_resolver"] = path_resolver

        _deferred_registry.setdefault(group, []).append({
            "name": tool_name, "func": func, "description": tool_desc,
            "group": group, "params": params, "tags": tags or [],
            "source": source, "meta": meta,
            "check_fn": check_fn, "allow_sleep": allow_sleep,
            "sleep_brief": sleep_brief,
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
