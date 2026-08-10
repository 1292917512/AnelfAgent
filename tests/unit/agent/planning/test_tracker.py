"""tracker plan 收敛测试：finalize_plan 双 outcome / cancel_plan 步骤收敛。

覆盖「全退出路径收敛」语义：
- completed：in_progress → completed，pending → skipped，plan → completed
- cancelled：in_progress / pending → skipped，plan → cancelled
- cancel_plan 取消时步骤同步收敛，前端不残留 in_progress
"""

from __future__ import annotations

import asyncio

from core.event_bus import (
    EVENT_PLAN_STATUS_CHANGED,
    EVENT_PLAN_STEP_UPDATED,
    event_bus,
)

SCOPE = "user_webui:test#chat1"


async def _setup(tmp_path):
    from agent.memory.memory_store import MemoryStore
    from agent.planning import tracker

    store = MemoryStore(db_path=str(tmp_path / "mem.db"))
    tracker.bind_store(store)
    return store, tracker


async def _teardown(store) -> None:
    from agent.planning import tracker

    await store.close()
    tracker._store = None


class TestFinalizePlan:
    async def test_completed_convergence(self, tmp_path):
        """正常收敛：in_progress→completed，pending→skipped，plan→completed。"""
        store, tracker = await _setup(tmp_path)
        try:
            plan_id = await tracker.submit_plan(
                SCOPE, "目标", tracker.parse_steps("a|b|c"),
            )
            await tracker.finalize_plan(SCOPE, "completed")

            # 收敛后不再是 active plan
            assert await tracker.get_active_plan(SCOPE) is None
            _, goal = await tracker.find_goal_by_id(plan_id)
            assert goal["status"] == "completed"
            assert [s["status"] for s in goal["steps"]] == [
                "completed", "skipped", "skipped",
            ]
        finally:
            await _teardown(store)

    async def test_cancelled_convergence(self, tmp_path):
        """中断收敛：in_progress/pending→skipped，plan→cancelled + 状态事件。"""
        store, tracker = await _setup(tmp_path)
        captured: list[dict] = []

        async def _capture(payload):
            captured.append(payload)

        event_bus.on(EVENT_PLAN_STATUS_CHANGED, _capture, owner="test.tracker")
        try:
            plan_id = await tracker.submit_plan(
                SCOPE, "目标", tracker.parse_steps("a|b|c"),
            )
            await tracker.finalize_plan(SCOPE, "cancelled")
            await asyncio.sleep(0.05)

            _, goal = await tracker.find_goal_by_id(plan_id)
            assert goal["status"] == "cancelled"
            assert [s["status"] for s in goal["steps"]] == ["skipped"] * 3
            assert captured[-1]["goal_status"] == "cancelled"
            assert captured[-1]["plan_id"] == plan_id
        finally:
            event_bus.off_by_owner("test.tracker")
            await _teardown(store)

    async def test_finalize_without_plan_is_noop(self, tmp_path):
        """无 active plan 时收敛零成本返回（finally 兜底安全）。"""
        store, tracker = await _setup(tmp_path)
        try:
            await tracker.finalize_plan(SCOPE)
            await tracker.finalize_plan(SCOPE, "cancelled")
        finally:
            await _teardown(store)

    async def test_finalize_is_idempotent(self, tmp_path):
        """重复收敛不重复发射状态事件（状态机幂等）。"""
        store, tracker = await _setup(tmp_path)
        captured: list[dict] = []

        async def _capture(payload):
            captured.append(payload)

        event_bus.on(EVENT_PLAN_STATUS_CHANGED, _capture, owner="test.tracker")
        try:
            await tracker.submit_plan(SCOPE, "目标", tracker.parse_steps("a"))
            await tracker.finalize_plan(SCOPE)
            await tracker.finalize_plan(SCOPE)
            await asyncio.sleep(0.05)
            assert len([c for c in captured if c["goal_status"] == "completed"]) == 1
        finally:
            event_bus.off_by_owner("test.tracker")
            await _teardown(store)


class TestCancelPlan:
    async def test_cancel_converges_steps(self, tmp_path):
        """cancel_plan 标记 cancelled 的同时把 in_progress 步骤收敛为 skipped。"""
        store, tracker = await _setup(tmp_path)
        captured: list[dict] = []

        async def _capture(payload):
            captured.append(payload)

        event_bus.on(EVENT_PLAN_STEP_UPDATED, _capture, owner="test.tracker")
        try:
            plan_id = await tracker.submit_plan(
                SCOPE, "目标", tracker.parse_steps("a|b"),
            )
            assert await tracker.cancel_plan(SCOPE, plan_id) is True
            await asyncio.sleep(0.05)

            _, goal = await tracker.find_goal_by_id(plan_id)
            assert goal["status"] == "cancelled"
            assert [s["status"] for s in goal["steps"]] == ["skipped", "skipped"]
            # in_progress → skipped 的步骤事件已发射
            assert any(
                c["plan_id"] == plan_id and c["step_status"] == "skipped"
                for c in captured
            )
        finally:
            event_bus.off_by_owner("test.tracker")
            await _teardown(store)

    async def test_cancel_unknown_plan_returns_false(self, tmp_path):
        """取消不存在的 plan 返回 False。"""
        store, tracker = await _setup(tmp_path)
        try:
            assert await tracker.cancel_plan(SCOPE, "nope") is False
        finally:
            await _teardown(store)


class TestUpdateGoalMetadata:
    async def test_metadata_merge_preserves_kind_scope(self, tmp_path):
        """update_goal 合并 metadata：kind/scope 不被覆盖（防计划跨 scope 泄漏）。"""
        store, tracker = await _setup(tmp_path)
        try:
            from agent.planning import tools as planning_tools
            planning_tools._store = store
            plan_id = await tracker.submit_plan(
                SCOPE, "目标", tracker.parse_steps("a|b"),
            )
            await planning_tools.update_goal(plan_id, step_index=0, step_status="completed")
            entry, goal = await tracker.find_goal_by_id(plan_id)
            assert entry is not None
            assert entry.metadata.get("kind") == "present_plan"
            assert entry.metadata.get("scope") == SCOPE
            assert entry.metadata.get("goal_id") == plan_id
        finally:
            from agent.planning import tools as planning_tools
            planning_tools._store = None
            await _teardown(store)

    async def test_reflect_scope_prefix_not_user_facing(self) -> None:
        """reflect:<id> 唯一 scope 同样视为非用户会话（不发射前端事件）。"""
        from agent.planning.tracker import _is_user_facing
        assert not _is_user_facing("reflect")
        assert not _is_user_facing("reflect:abc12345")
        assert _is_user_facing("user_webui:u1")
