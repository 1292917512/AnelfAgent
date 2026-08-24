"""统一的 runtime 访问层（web/services 侧门面）。

实现归 agent.runtime.singleton / agent.runtime.agent_app（单一权威），
本模块仅为 services/web 层提供不触发懒创建的读取语义。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent.runtime.agent_app import AgentApp
    from agent.runtime.runtime import AgentRuntime


def is_ready() -> bool:
    """检查 AgentRuntime 是否已初始化（不触发懒创建）。"""
    from agent.runtime.singleton import get_runtime
    return get_runtime() is not None


def get_runtime() -> Optional["AgentRuntime"]:
    """获取 runtime；未就绪返回 None。"""
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
    from agent.runtime.singleton import require_runtime as _require
    return _require()
