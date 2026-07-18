"""委托管理器 — 子代理的并发调度、预算控制与结果聚合。

- 并发上限：asyncio.Semaphore（默认 3，可配置）
- 并行模式：tasks 数组 fan-out，asyncio.gather 并发执行
- 预算控制：每个子代理独立的迭代预算（默认 15 轮）
- 结果聚合：按 task_index 排序，摘要按父上下文剩余空间动态截断
- 后台模式：立即返回 delegation_id，结果经事件总线异步推送
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.event_bus import event_bus
from core.log import log

from agent.delegation.sub_agent import SubAgent, SubAgentResult, normalize_role

if TYPE_CHECKING:
    from agent.mind.mind import Mind

EVENT_DELEGATION_COMPLETED = "delegation.completed"

# 结果摘要预算（参考 hermes：父上下文剩余空间的 50% 均分给各子任务）
_SUMMARY_HEADROOM_FRACTION = 0.5
_MIN_SUMMARY_CHARS = 2_000
_MAX_SUMMARY_CHARS = 24_000
_CHARS_PER_TOKEN = 4


def _max_concurrent() -> int:
    from core.config import get_config_int
    return max(1, get_config_int("delegation_max_concurrent", 3))


class DelegationManager:
    """子代理委托管理器。"""

    def __init__(self, mind: "Mind") -> None:
        self._mind = mind
        self._semaphore = asyncio.Semaphore(_max_concurrent())
        self._background_tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # 同步委托
    # ------------------------------------------------------------------

    async def delegate(
            self,
            goal: str,
            context: str = "",
            *,
            role: str = "leaf",
            max_iterations: int = 0,
            task_index: int = 0,
    ) -> SubAgentResult:
        """委托单个子任务（阻塞至完成）。"""
        async with self._semaphore:
            agent = SubAgent(
                self._mind, goal, context,
                role=role, max_iterations=max_iterations, task_index=task_index,
            )
            return await agent.run()

    async def delegate_batch(
            self,
            tasks: List[Dict[str, str]],
            *,
            role: str = "leaf",
            max_iterations: int = 0,
    ) -> List[SubAgentResult]:
        """并行委托多个子任务，结果按 task_index 排序。"""
        if len(tasks) > _max_concurrent() * 3:
            raise ValueError(
                f"并行子任务数量超限（{len(tasks)} > {_max_concurrent() * 3}），请拆分批次"
            )
        results = await asyncio.gather(
            *(
                self.delegate(
                    t.get("goal", ""), t.get("context", ""),
                    role=normalize_role(t.get("role") or role),
                    max_iterations=max_iterations,
                    task_index=i,
                )
                for i, t in enumerate(tasks)
            ),
            return_exceptions=True,
        )
        final: List[SubAgentResult] = []
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                final.append(SubAgentResult(
                    goal=tasks[i].get("goal", ""), success=False,
                    error=f"{type(r).__name__}: {r}", task_index=i,
                ))
            else:
                final.append(r)
        final.sort(key=lambda r: r.task_index)
        return final

    # ------------------------------------------------------------------
    # 后台委托
    # ------------------------------------------------------------------

    def delegate_background(
            self,
            goal: str,
            context: str = "",
            *,
            role: str = "leaf",
            max_iterations: int = 0,
    ) -> str:
        """后台委托：立即返回 delegation_id，结果经事件总线异步推送。"""
        delegation_id = uuid.uuid4().hex[:8]
        task = asyncio.create_task(
            self._run_background(delegation_id, goal, context, role, max_iterations),
            name=f"delegation.{delegation_id}",
        )
        self._background_tasks[delegation_id] = task
        task.add_done_callback(lambda _: self._background_tasks.pop(delegation_id, None))
        log(f"后台委托已启动: {delegation_id} -> {goal[:60]}", tag="委托")
        return delegation_id

    async def _run_background(
            self,
            delegation_id: str,
            goal: str,
            context: str,
            role: str,
            max_iterations: int,
    ) -> None:
        """后台执行委托并推送结果事件。"""
        result = await self.delegate(
            goal, context, role=role, max_iterations=max_iterations,
        )
        payload = {
            "delegation_id": delegation_id,
            "goal": goal,
            "success": result.success,
            "output": result.output,
            "error": result.error,
        }
        await event_bus.emit(EVENT_DELEGATION_COMPLETED, payload)
        # 结果写入短期记忆，主 Agent 下一轮思考时可见
        status = "成功" if result.success else "失败"
        summary = result.output if result.success else result.error
        self._mind.pfc.add_temporary({
            "role": "user",
            "content": (
                f"[后台委托完成] id={delegation_id} 状态={status}\n"
                f"目标: {goal[:200]}\n结果: {summary[:1500]}"
            ),
        })
        log(f"后台委托完成: {delegation_id} ({status})", tag="委托")

    # ------------------------------------------------------------------
    # 结果聚合
    # ------------------------------------------------------------------

    def aggregate_results(self, results: List[SubAgentResult]) -> str:
        """聚合子代理结果为工具返回（JSON），摘要按父上下文预算截断。"""
        budget = self._summary_char_budget(len(results))
        items: List[Dict[str, Any]] = []
        for r in results:
            output = r.output
            if len(output) > budget:
                output = self._trim_summary(output, budget)
            items.append({
                "task_index": r.task_index,
                "goal": r.goal,
                "success": r.success,
                "output": output,
                **({"error": r.error} if r.error else {}),
            })
        succeeded = sum(1 for r in results if r.success)
        return json.dumps({
            "ok": succeeded == len(results),
            "total": len(results),
            "succeeded": succeeded,
            "failed": len(results) - succeeded,
            "results": items,
        }, ensure_ascii=False)

    def _summary_char_budget(self, n_summaries: int) -> int:
        """每个子任务摘要的字符预算（父上下文剩余空间均分，参考 hermes）。"""
        context_length = self._mind.get_model_context_length()
        if context_length <= 0:
            return _MAX_SUMMARY_CHARS
        headroom_chars = context_length * _CHARS_PER_TOKEN
        per_summary = int(headroom_chars * _SUMMARY_HEADROOM_FRACTION) // max(1, n_summaries)
        return max(_MIN_SUMMARY_CHARS, min(per_summary, _MAX_SUMMARY_CHARS))

    @staticmethod
    def _trim_summary(text: str, budget: int) -> str:
        """摘要截断：保留头部 75% + 尾部 25% + 截断标记。"""
        head = int(budget * 0.75)
        tail = budget - head
        return (
            f"{text[:head]}\n"
            f"...[摘要过长已截断，原长度={len(text)} 字符]...\n"
            f"{text[-tail:]}"
        )


# ------------------------------------------------------------------
# 配置注册
# ------------------------------------------------------------------

_DELEGATION_CONFIGS = {
    "子代理": {
        "delegation_enabled": {
            "description": "是否启用子代理委托",
            "default": True,
        },
        "delegation_max_depth": {
            "description": "最大委托深度（orchestrator 可再委托的层数）",
            "default": 2,
        },
        "delegation_max_concurrent": {
            "description": "子代理并发上限",
            "default": 3,
        },
        "delegation_default_iterations": {
            "description": "子代理默认迭代预算（轮次）",
            "default": 15,
        },
    },
}

from core.config import register_configs_safe  # noqa: E402

register_configs_safe(_DELEGATION_CONFIGS)
