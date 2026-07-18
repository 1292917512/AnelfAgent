"""子代理调度系统（agent.delegation）单元测试。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from agent.delegation.delegate_tool import delegate_task, register_delegation_tools
from agent.delegation.delegation_manager import DelegationManager
from agent.delegation.sub_agent import (
    SubAgent,
    _delegate_depth,
    current_depth,
    max_spawn_depth,
    normalize_role,
)


class _FakeMind:
    """最小 Mind 替身：reflect 直接返回结果。"""

    def __init__(self, output: str = "子任务完成报告") -> None:
        self.reflect = AsyncMock(return_value=output)
        self.pfc = type("PFC", (), {"add_temporary": lambda self, clip: None})()

    def get_model_context_length(self) -> int:
        return 128_000


@pytest.fixture
def manager() -> DelegationManager:
    return DelegationManager(_FakeMind())


class TestSubAgent:
    async def test_run_success(self) -> None:
        mind = _FakeMind("调研结果：xxx")
        agent = SubAgent(mind, "调研主题", "背景", task_index=0)
        result = await agent.run()
        assert result.success
        assert result.output == "调研结果：xxx"
        # reflect 被调用且禁止外发
        kwargs = mind.reflect.call_args.kwargs
        assert kwargs["allow_output_tools"] is False

    async def test_leaf_blocks_delegate(self) -> None:
        mind = _FakeMind()
        agent = SubAgent(mind, "任务", role="leaf")
        await agent.run()
        kwargs = mind.reflect.call_args.kwargs
        assert "delegate_task" in kwargs["extra_blocked_tools"]

    async def test_orchestrator_keeps_delegate(self) -> None:
        mind = _FakeMind()
        agent = SubAgent(mind, "任务", role="orchestrator")
        await agent.run()
        kwargs = mind.reflect.call_args.kwargs
        assert kwargs["extra_blocked_tools"] is None

    async def test_empty_output_is_failure(self) -> None:
        mind = _FakeMind("")
        agent = SubAgent(mind, "任务")
        result = await agent.run()
        assert not result.success

    async def test_exception_is_failure(self) -> None:
        mind = _FakeMind()
        mind.reflect = AsyncMock(side_effect=RuntimeError("boom"))
        agent = SubAgent(mind, "任务")
        result = await agent.run()
        assert not result.success and "boom" in result.error

    async def test_depth_incremented_and_reset(self) -> None:
        mind = _FakeMind()
        observed = []

        async def capture(*args, **kwargs):
            observed.append(current_depth())
            return "ok"

        mind.reflect = capture
        agent = SubAgent(mind, "任务")
        await agent.run()
        assert observed == [1]
        assert current_depth() == 0


class TestDelegationManager:
    async def test_delegate_single(self, manager: DelegationManager) -> None:
        result = await manager.delegate("任务A")
        assert result.success

    async def test_delegate_batch_ordered(self, manager: DelegationManager) -> None:
        tasks = [{"goal": f"任务{i}"} for i in range(3)]
        results = await manager.delegate_batch(tasks)
        assert len(results) == 3
        assert [r.task_index for r in results] == [0, 1, 2]

    async def test_batch_size_limit(self, manager: DelegationManager) -> None:
        tasks = [{"goal": f"任务{i}"} for i in range(20)]
        with pytest.raises(ValueError):
            await manager.delegate_batch(tasks)

    async def test_aggregate_results(self, manager: DelegationManager) -> None:
        results = await manager.delegate_batch([{"goal": "A"}, {"goal": "B"}])
        aggregated = json.loads(manager.aggregate_results(results))
        assert aggregated["total"] == 2
        assert aggregated["succeeded"] == 2
        assert aggregated["results"][0]["output"] == "子任务完成报告"

    async def test_summary_budget_trims_long_output(self) -> None:
        mind = _FakeMind("x" * 100_000)
        manager = DelegationManager(mind)
        results = await manager.delegate_batch([{"goal": "A"}])
        aggregated = json.loads(manager.aggregate_results(results))
        output = aggregated["results"][0]["output"]
        assert len(output) <= 24_000 + 200
        assert "已截断" in output

    async def test_background_delegation(self, manager: DelegationManager) -> None:
        import asyncio
        delegation_id = manager.delegate_background("后台任务")
        assert len(delegation_id) == 8
        task = manager._background_tasks[delegation_id]
        await asyncio.wait_for(task, timeout=5)


class TestDelegateTool:
    async def test_depth_limit(self, manager: DelegationManager) -> None:
        register_delegation_tools(manager)
        token = _delegate_depth.set(max_spawn_depth())
        try:
            result = json.loads(await delegate_task(goal="任务"))
            assert "error" in result
            assert "深度" in result["error"]
        finally:
            _delegate_depth.reset(token)

    async def test_single_goal(self, manager: DelegationManager) -> None:
        register_delegation_tools(manager)
        result = json.loads(await delegate_task(goal="调研xxx"))
        assert result["total"] == 1

    async def test_batch_tasks(self, manager: DelegationManager) -> None:
        register_delegation_tools(manager)
        tasks = json.dumps([{"goal": "A"}, {"goal": "B"}])
        result = json.loads(await delegate_task(tasks=tasks))
        assert result["total"] == 2

    async def test_invalid_tasks_json(self, manager: DelegationManager) -> None:
        register_delegation_tools(manager)
        result = json.loads(await delegate_task(tasks="not json"))
        assert "error" in result

    async def test_missing_goal(self, manager: DelegationManager) -> None:
        register_delegation_tools(manager)
        result = json.loads(await delegate_task())
        assert "error" in result

    async def test_background_mode(self, manager: DelegationManager) -> None:
        register_delegation_tools(manager)
        result = json.loads(await delegate_task(goal="后台任务", background=True))
        assert result["mode"] == "background"
        assert "delegation_id" in result
        # 等待后台任务完成，避免泄漏
        task = manager._background_tasks.get(result["delegation_id"])
        if task:
            import asyncio
            await asyncio.wait_for(task, timeout=5)


class TestRoleNormalization:
    def test_normalize_role(self) -> None:
        assert normalize_role("leaf") == "leaf"
        assert normalize_role("orchestrator") == "orchestrator"
        assert normalize_role("unknown") == "leaf"
        assert normalize_role(None) == "leaf"
