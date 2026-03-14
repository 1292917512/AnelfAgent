from __future__ import annotations

from typing import Optional

from agent.core.runtime.runtime import AgentRuntime

_default_runtime: Optional[AgentRuntime] = None


def get_runtime() -> AgentRuntime:
    """获取全局 AgentRuntime。未初始化时抛出 RuntimeError。"""
    if _default_runtime is None:
        raise RuntimeError(
            "AgentRuntime 尚未初始化，请确保 bootstrap 已执行"
        )
    return _default_runtime


def set_runtime(runtime: AgentRuntime) -> None:
    global _default_runtime
    _default_runtime = runtime


def reset_runtime() -> None:
    """重置全局 Runtime（测试用）。"""
    global _default_runtime
    _default_runtime = None
