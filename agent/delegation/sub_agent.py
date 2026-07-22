"""子代理 — 隔离执行的二级思维单元（参考 hermes-agent delegate_tool）。

子代理通过 mind.reflect() 在隔离的上下文中执行子任务：
- 独立的消息上下文（不污染主对话）
- 受限工具集（默认禁止外发消息）
- 角色模型：leaf（不可再委托）/ orchestrator（可再委托，受深度限制）
- 独立迭代预算（防止无限循环）

委托深度通过 contextvars 跟踪，异步任务间隔离。
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind

# 当前委托深度（主 Agent 为 0，每委托一层 +1）
_delegate_depth: ContextVar[int] = ContextVar("delegate_depth", default=0)

_ROLE_LEAF = "leaf"
_ROLE_ORCHESTRATOR = "orchestrator"
_VALID_ROLES = frozenset({_ROLE_LEAF, _ROLE_ORCHESTRATOR})

_DEFAULT_MAX_ITERATIONS = 15


@dataclass
class SubAgentResult:
    """子代理执行结果。"""

    goal: str
    success: bool
    output: str = ""
    error: str = ""
    role: str = _ROLE_LEAF
    task_index: int = 0

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "role": self.role,
            "task_index": self.task_index,
        }


def current_depth() -> int:
    """当前委托深度（主 Agent 为 0）。"""
    return _delegate_depth.get()


def normalize_role(role: Optional[str]) -> str:
    """规范化角色名（未知角色降级为 leaf）。"""
    return role if role in _VALID_ROLES else _ROLE_LEAF


def max_spawn_depth() -> int:
    """最大委托深度（配置 delegation_max_depth，默认 2）。"""
    from core.config import get_config_int
    return max(1, get_config_int("delegation_max_depth", 2))


def default_max_iterations() -> int:
    """子代理默认迭代预算。"""
    from core.config import get_config_int
    return get_config_int("delegation_default_iterations", _DEFAULT_MAX_ITERATIONS)


def clamp_iterations(value: int) -> int:
    """钳制迭代预算到 [1, 硬上限]（<=0 时按默认预算再钳制）。"""
    from core.config import get_config_int
    cap = max(1, get_config_int("delegation_max_iterations_cap", 50))
    budget = value if value > 0 else default_max_iterations()
    return max(1, min(budget, cap))


def delegation_timeout_seconds() -> float:
    """单个子代理整体执行超时（配置 delegation_timeout_seconds，默认 600s）。"""
    from core.config import get_config_float
    return max(1.0, get_config_float("delegation_timeout_seconds", 600.0))


_SUB_AGENT_PROMPT = """你是一个子代理，负责完成主代理委托的子任务。

[子任务目标]
{goal}

[背景上下文]
{context}

[执行要求]
1. 专注于完成上述子任务，不要偏离目标
2. 你可以使用工具完成查询、计算、分析等操作
3. 完成后，用一段清晰的文字总结结果（主代理只能看到你的文字总结）
4. 总结必须自包含：主代理看不到你的中间过程，关键数据和结论都要写进总结
{role_hint}
"""


class SubAgent:
    """子代理：在隔离上下文中执行单个子任务。"""

    def __init__(
            self,
            mind: "Mind",
            goal: str,
            context: str = "",
            *,
            role: str = _ROLE_LEAF,
            max_iterations: int = 0,
            task_index: int = 0,
    ) -> None:
        self._mind = mind
        self.goal = goal
        self.context = context
        self.role = normalize_role(role)
        self.max_iterations = clamp_iterations(max_iterations)
        self.task_index = task_index

    async def run(self) -> SubAgentResult:
        """执行子任务并返回结果摘要。"""
        depth = current_depth()
        log(
            f"子代理启动 (depth={depth}, role={self.role}, 预算={self.max_iterations}轮): "
            f"{self.goal[:80]}",
            tag="委托",
        )

        role_hint = (
            "5. 你是 orchestrator 角色：如子任务过于复杂，可调用 delegate_task 进一步拆分委托。"
            if self.role == _ROLE_ORCHESTRATOR
            else "5. 你是 leaf 角色：不可再委托，必须自己完成全部工作。"
        )
        prompt = _SUB_AGENT_PROMPT.format(
            goal=self.goal,
            context=self.context or "（无额外背景）",
            role_hint=role_hint,
        )

        # leaf 角色禁止再委托（orchestrator 保留 delegate_task，深度由工具自身硬限制）
        extra_blocked = {"delegate_task"} if self.role == _ROLE_LEAF else None

        token = _delegate_depth.set(depth + 1)
        timeout = delegation_timeout_seconds()
        try:
            output = await asyncio.wait_for(
                self._mind.reflect(
                    [{"role": "user", "content": prompt}],
                    max_iterations=self.max_iterations,
                    allow_output_tools=False,
                    extra_blocked_tools=extra_blocked,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log(
                f"子代理整体超时（>{timeout:.0f}s）: {self.goal[:60]}",
                "WARNING", tag="委托",
            )
            return SubAgentResult(
                goal=self.goal, success=False,
                error=f"子代理执行超时（>{timeout:.0f}s），已中断",
                role=self.role, task_index=self.task_index,
            )
        except Exception as exc:
            log(f"子代理失败: {self.goal[:60]}: {type(exc).__name__}: {exc}", "WARNING", tag="委托")
            return SubAgentResult(
                goal=self.goal, success=False,
                error=f"{type(exc).__name__}: {exc}",
                role=self.role, task_index=self.task_index,
            )
        finally:
            _delegate_depth.reset(token)
        output = (output or "").strip()
        if not output:
            return SubAgentResult(
                goal=self.goal, success=False,
                error="子代理未产出任何结果",
                role=self.role, task_index=self.task_index,
            )
        log(f"子代理完成: {self.goal[:60]} -> {len(output)} 字", tag="委托")
        return SubAgentResult(
            goal=self.goal, success=True, output=output,
            role=self.role, task_index=self.task_index,
        )
