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

from typing import Any, Callable, List, Optional, TypeVar

from core.entity import EntityRegistry
from core.tool_schema import extract_tool_params, get_first_line

# 兼容别名：历史引用（含测试）使用私有名
_extract_params = extract_tool_params
_get_first_line = get_first_line

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
) -> Callable[[F], F]:
    """装饰器：将函数注册为 LLM 可调用工具（注册到 EntityRegistry）。

    参数的名称、类型、是否必填从函数签名自动推导。

    Args:
        timeout: 工具执行超时时间（秒），默认使用全局配置（30秒）
        check_fn: 工具门控前置检查（返回 bool 或 Awaitable[bool]），
            检查不通过时工具不出现在 LLM schema 中
        allow_sleep: 是否允许沉睡（沉睡时仅展示 sleep_brief）
        sleep_brief: 沉睡状态下展示给 AI 的简短描述
        concurrency_safe: 是否可与其他安全工具并行执行（只读工具才应开启，
            默认 False — 与 Claude Code isConcurrencySafe 一致的 fail-closed 语义）
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or _get_first_line(func.__doc__) or tool_name
        params = _extract_params(func)

        meta = {}
        if timeout is not None:
            meta["timeout"] = timeout
        if concurrency_safe:
            meta["concurrency_safe"] = True

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
) -> Callable[[F], F]:
    """延迟注册装饰器：装饰时仅收集元数据，activate_group() 时批量注册。

    用于需要运行时依赖注入的工具（如 MemoryStore、Embedder 等）。
    参数名称、类型、描述从函数签名和 docstring 自动推导。

    Args:
        timeout: 工具执行超时时间（秒），默认使用全局配置（30秒）
        check_fn: 工具门控前置检查（返回 bool 或 Awaitable[bool]）
        allow_sleep: 是否允许沉睡（沉睡时仅展示 sleep_brief）
        sleep_brief: 沉睡状态下展示给 AI 的简短描述
        concurrency_safe: 是否可与其他安全工具并行执行（只读工具才应开启）
    """
    def decorator(func: F) -> F:
        tool_name = name or func.__name__
        tool_desc = description or _get_first_line(func.__doc__) or tool_name
        params = _extract_params(func)

        meta = {}
        if timeout is not None:
            meta["timeout"] = timeout
        if concurrency_safe:
            meta["concurrency_safe"] = True

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


# ------------------------------------------------------------------
# LLM 桥接（延迟导入 agent.llm，供 entities 层使用）
# ------------------------------------------------------------------


def get_llm_manager() -> Any:
    """获取 LLMManager 实例（延迟导入 agent.llm）。"""
    from agent.llm import get_llm_manager as _get
    return _get()


def get_current_scope() -> str:
    """获取当前对话 scope（延迟导入 agent.mind，未绑定时返回 "_global"）。

    供 entities 层工具按 scope 隔离会话状态（如文件读取缓存）。
    在思维会话外调用（测试、心跳等）时返回全局作用域。
    """
    try:
        from agent.mind.tool_activation import ToolActivationManager
        return ToolActivationManager.current_scope()
    except Exception:
        return "_global"


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

