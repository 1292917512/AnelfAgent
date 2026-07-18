"""子代理调度系统：复杂任务拆分委托与并行执行。

- sub_agent:          子代理（隔离上下文执行，leaf/orchestrator 角色）
- delegation_manager: 并发调度、预算控制、结果聚合、后台模式
- delegate_tool:      AI 可调用的 delegate_task 工具
"""

from agent.delegation.delegate_tool import register_delegation_tools
from agent.delegation.delegation_manager import DelegationManager
from agent.delegation.sub_agent import SubAgent, SubAgentResult

__all__ = [
    "DelegationManager",
    "SubAgent",
    "SubAgentResult",
    "register_delegation_tools",
]
