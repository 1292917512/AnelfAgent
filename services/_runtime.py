"""统一的 runtime 访问层。

所有 Service 通过本模块安全地获取 AgentRuntime / AgentApp，
不会意外触发懒创建（避免竞态和重复初始化）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent.runtime.agent_app import AgentApp
    from agent.runtime.runtime import AgentRuntime


def is_ready() -> bool:
    """检查 AgentRuntime 是否已初始化（不触发懒创建）。"""
    try:
        from agent.runtime import singleton
        return singleton._default_runtime is not None
    except Exception:
        return False


def get_runtime() -> Optional["AgentRuntime"]:
    """获取 runtime；未就绪返回 None。"""
    if not is_ready():
        return None
    from agent.runtime.singleton import get_runtime as _get
    return _get()


def get_agent_app() -> Optional["AgentApp"]:
    """获取 AgentApp；未就绪返回 None。"""
    if not is_ready():
        return None
    from agent.runtime.agent_app import get_agent_app as _get
    return _get()


def require_runtime() -> "AgentRuntime":
    """获取 runtime；未就绪时抛出 RuntimeError。"""
    rt = get_runtime()
    if rt is None:
        raise RuntimeError("AgentRuntime 尚未初始化")
    return rt
