"""工作流测试共享件：假委托管理器 / Stub Mind / 等待助手（同目录模块，非 conftest）。"""

from __future__ import annotations

import asyncio
import uuid

from agent.delegation.sub_agent import SubAgentResult
from agent.mind.background_tasks import BackgroundTaskRegistry


class FakeDelegationManager:
    """可编排的委托管理器假件：按 goal 记录调用、可阻塞、可编排失败。"""

    def __init__(self) -> None:
        self.calls: list = []  # {"goal", "context", "base_messages"}
        self.ids: list = []
        # goal → 阻塞 Event（在飞等待用）
        self.gates: dict = {}
        # goal → 前 N 次失败的剩余次数
        self.failures: dict = {}

    async def delegate(self, goal, context="", *, role="leaf", max_iterations=0,
                       task_index=0, scope_hint="", difficulty=0, delegation_id="",
                       agent_name="", emit_events=True, fork_context=False, facets=None,
                       base_messages=None, parent_delegation_id=""):
        self.calls.append({"goal": goal, "context": context,
                           "base_messages": base_messages})
        did = delegation_id or uuid.uuid4().hex[:8]
        self.ids.append(did)
        if goal in self.gates:
            await self.gates[goal].wait()
        remaining = self.failures.get(goal, 0)
        if remaining > 0:
            self.failures[goal] = remaining - 1
            return SubAgentResult(goal=goal, success=False, error=f"编排失败: {goal}")
        return SubAgentResult(
            goal=goal, success=True, output=f"out[{goal}]",
            messages=[{"role": "assistant", "content": f"done {goal}"}],
            usage={"turns": 2, "input_tokens": 100, "output_tokens": 50, "duration_ms": 800},
        )


def make_stub_mind(manager: FakeDelegationManager) -> object:
    """构造引擎依赖的最小 Mind 桩（delegation_manager + background_tasks）。"""
    return type("StubMind", (), {
        "delegation_manager": manager,
        "background_tasks": BackgroundTaskRegistry(),
    })()



async def wait_until(predicate, timeout: float = 5.0, interval: float = 0.02) -> None:
    """轮询等待条件成立（支持同步与异步谓词；超时抛 AssertionError）。"""
    import inspect

    async def _check() -> bool:
        result = predicate()
        if inspect.iscoroutine(result):
            return await result
        return bool(result)

    deadline = asyncio.get_event_loop().time() + timeout
    while not await _check():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError("wait_until 超时")
        await asyncio.sleep(interval)


async def wait_run_terminal(journal, run_id: str, timeout: float = 8.0) -> dict:
    """等待 run 进入终态并返回 run 行。"""
    async def _terminal() -> bool:
        run = await journal.get_run(run_id)
        return run is not None and run["status"] != "running"
    await wait_until(_terminal, timeout=timeout)
    return await journal.get_run(run_id)
