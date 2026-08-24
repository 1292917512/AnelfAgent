"""子代理调度系统（agent.delegation）单元测试。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agent.delegation.delegate_tool import delegate_task, delegation_manager_port
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
        self.pfc = type("PFC", (), {"add_temporary": lambda self, clip, scope="": None})()

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
        delegation_manager_port.set(manager)
        token = _delegate_depth.set(max_spawn_depth())
        try:
            result = json.loads(await delegate_task(goal="任务"))
            assert "error" in result
            assert "深度" in result["error"]
        finally:
            _delegate_depth.reset(token)

    async def test_single_goal(self, manager: DelegationManager) -> None:
        delegation_manager_port.set(manager)
        result = json.loads(await delegate_task(goal="调研xxx"))
        assert result["total"] == 1

    async def test_batch_tasks(self, manager: DelegationManager) -> None:
        delegation_manager_port.set(manager)
        tasks = json.dumps([{"goal": "A"}, {"goal": "B"}])
        result = json.loads(await delegate_task(tasks=tasks))
        assert result["total"] == 2

    async def test_invalid_tasks_json(self, manager: DelegationManager) -> None:
        delegation_manager_port.set(manager)
        result = json.loads(await delegate_task(tasks="not json"))
        assert "error" in result

    async def test_missing_goal(self, manager: DelegationManager) -> None:
        delegation_manager_port.set(manager)
        result = json.loads(await delegate_task())
        assert "error" in result

    async def test_background_mode(self, manager: DelegationManager) -> None:
        delegation_manager_port.set(manager)
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


def _slow_mind(seconds: float = 60.0) -> _FakeMind:
    """reflect 长时间阻塞的 Mind 替身（取消场景用）。"""
    mind = _FakeMind()

    async def slow(*args: object, **kwargs: object) -> str:
        await asyncio.sleep(seconds)
        return "不应到达"

    mind.reflect = slow
    return mind


class TestDelegationCancel:
    async def test_cancel_running_sync_delegation(self) -> None:
        manager = DelegationManager(_slow_mind())
        task = asyncio.create_task(manager.delegate("长任务"))
        await asyncio.sleep(0.1)
        delegation_id = next(iter(manager._running))
        assert manager.cancel(delegation_id) is True
        result = await asyncio.wait_for(task, timeout=5)
        assert not result.success
        assert result.cancelled
        assert "取消" in result.error
        # 取消结果经聚合携带 user_cancel 归因，提示 AI 不要自动重试
        aggregated = json.loads(manager.aggregate_results([result]))
        item = aggregated["results"][0]
        assert item["cause"] == "user_cancel"
        assert item["retryable"] is False

    async def test_cancel_unknown_returns_false(self, manager: DelegationManager) -> None:
        assert manager.cancel("deadbeef") is False

    async def test_cancel_scope_cancels_all(self) -> None:
        manager = DelegationManager(_slow_mind())
        t1 = asyncio.create_task(manager.delegate("任务1"))
        t2 = asyncio.create_task(manager.delegate("任务2"))
        await asyncio.sleep(0.1)
        assert manager.cancel_scope("_global") == 2
        r1, r2 = await asyncio.wait_for(asyncio.gather(t1, t2), timeout=5)
        assert r1.cancelled and r2.cancelled
        # 非目标 scope 不受影响
        assert manager.cancel_scope("user_webui:web_user") == 0

    async def test_running_snapshot(self) -> None:
        manager = DelegationManager(_slow_mind())
        task = asyncio.create_task(manager.delegate("快照任务"))
        await asyncio.sleep(0.1)
        snapshot = manager.running_snapshot("_global")
        assert len(snapshot) == 1
        assert snapshot[0]["goal"] == "快照任务"
        assert snapshot[0]["role"] == "leaf"
        manager.cancel(snapshot[0]["delegation_id"])
        await asyncio.wait_for(task, timeout=5)
        assert manager.running_snapshot("_global") == []

    async def test_cancel_background_delegation(self) -> None:
        manager = DelegationManager(_slow_mind())
        delegation_id = manager.delegate_background(
            "后台长任务", scope="user_webui:web_user",
        )
        await asyncio.sleep(0.1)
        assert manager.cancel(delegation_id) is True
        task = manager._background_tasks.get(delegation_id)
        assert task is not None
        # 取消被转化为取消结果并正常路由（任务正常结束，不抛 CancelledError）
        await asyncio.wait_for(task, timeout=5)


class TestDelegationProgress:
    async def test_progress_events_emitted(self) -> None:
        from core.event_bus import (
            EVENT_DELEGATION_PROGRESS,
            EVENT_THINKING_TOOL_START,
            event_bus,
        )

        mind = _FakeMind()
        received: list[dict] = []

        async def capture(payload: dict) -> None:
            received.append(payload)

        event_bus.on(EVENT_DELEGATION_PROGRESS, capture, owner="test_delegation_progress")
        try:
            manager = DelegationManager(mind)

            async def reflect_with_tool_event(*args: object, **kwargs: object) -> str:
                await event_bus.emit(EVENT_THINKING_TOOL_START, {"tool_name": "web_search"})
                return "ok"

            mind.reflect = reflect_with_tool_event
            result = await manager.delegate("进度任务")
            assert result.success
            starts = [p for p in received if p.get("kind") == "tool_start"]
            assert len(starts) == 1
            assert starts[0]["tool"] == "web_search"
            assert starts[0]["delegation_id"]
        finally:
            event_bus.off_by_owner("test_delegation_progress")

    async def test_no_progress_without_delegation(self, manager: DelegationManager) -> None:
        from core.event_bus import (
            EVENT_DELEGATION_PROGRESS,
            EVENT_THINKING_TOOL_START,
            event_bus,
        )

        received: list[dict] = []

        async def capture(payload: dict) -> None:
            received.append(payload)

        event_bus.on(EVENT_DELEGATION_PROGRESS, capture, owner="test_delegation_idle")
        try:
            # 主 Agent 上下文（无委托 ID 绑定）发射工具事件 → 不产生进度事件
            await event_bus.emit(EVENT_THINKING_TOOL_START, {"tool_name": "web_search"})
            assert received == []
        finally:
            event_bus.off_by_owner("test_delegation_idle")
