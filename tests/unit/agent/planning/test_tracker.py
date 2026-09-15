"""tracker plan 收敛测试：finalize_plan 双 outcome / cancel_plan 步骤收敛 / 生命周期隔离。

覆盖「全退出路径收敛」语义：
- completed：in_progress → completed，pending → skipped，plan → completed
- cancelled：in_progress / pending → skipped，plan → cancelled
- cancel_plan 取消时步骤同步收敛，前端不残留 in_progress

生命周期隔离（回归 2026-09-15 ebafea16 事故）：
- create_goal 持久目标（GOAL_KIND，跨会话）不被任何 scope 的自动
  推进/会话收敛触碰——目标只由 AI 显式标记或 delete_goal 终结
- 会话计划收敛严格按 scope 匹配，跨 scope 不误伤
"""

from __future__ import annotations

import asyncio
import json

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
    tracker.planning_store_port.set(store)
    return store, tracker


async def _teardown(store) -> None:
    from agent.planning import tracker

    await store.close()
    tracker.planning_store_port.unbind()


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


class TestLifecycleIsolation:
    """会话计划状态机与持久目标（GOAL_KIND）的生命周期隔离。"""

    async def test_finalize_never_converges_goals(self, tmp_path):
        """create_goal 持久目标不被任何 scope 的退出收敛触碰（连带收口回归）。"""
        store, tracker = await _setup(tmp_path)
        try:
            from agent.planning import tools as planning_tools
            raw = await planning_tools.create_goal("夜间目标", steps="a|b|c")
            goal_id = json.loads(raw)["goal"]["goal_id"]

            await tracker.finalize_plan(SCOPE, "completed")
            await tracker.finalize_plan("reflect:task001", "completed")

            _, goal = await tracker.find_goal_by_id(goal_id)
            assert goal["status"] == "active"
            assert [s["status"] for s in goal["steps"]] == ["pending"] * 3
        finally:
            await _teardown(store)

    async def test_advance_never_touches_goals(self, tmp_path):
        """每轮自动推进不触碰目标步骤——update_goal 推进一步后，
        后续轮次的工具批次不得把 in_progress 步骤连带标完成。"""
        store, tracker = await _setup(tmp_path)
        try:
            from agent.mind.tool_activation import bind_scope, reset_scope
            from agent.planning import tools as planning_tools
            raw = await planning_tools.create_goal("夜间目标", steps="a|b|c")
            goal_id = json.loads(raw)["goal"]["goal_id"]
            await planning_tools.update_goal(
                goal_id, step_index=0, step_status="completed",
            )

            token = bind_scope(SCOPE)
            try:
                await tracker.advance_plan_step(SCOPE)
            finally:
                reset_scope(token)

            _, goal = await tracker.find_goal_by_id(goal_id)
            # step0 completed（AI 标记）+ step1 in_progress（update_goal 自动推进），
            # 其余保持 pending——没有程序级连带推进
            assert [s["status"] for s in goal["steps"]] == [
                "completed", "in_progress", "pending",
            ]
        finally:
            await _teardown(store)

    async def test_finalize_scope_strict_match(self, tmp_path):
        """会话收敛只作用于同 scope 的计划，无关 scope 退出不误伤。"""
        store, tracker = await _setup(tmp_path)
        try:
            plan_id = await tracker.submit_plan(
                SCOPE, "目标", tracker.parse_steps("a|b"),
            )
            await tracker.finalize_plan("user_other:1", "completed")

            _, goal = await tracker.find_goal_by_id(plan_id)
            assert goal["status"] == "active"

            await tracker.finalize_plan(SCOPE, "completed")
            _, goal = await tracker.find_goal_by_id(plan_id)
            assert goal["status"] == "completed"
        finally:
            await _teardown(store)

    async def test_goal_does_not_block_plan_machinery(self, tmp_path):
        """存在活跃目标时不影响会话计划的提交/收敛/复用判定。"""
        store, tracker = await _setup(tmp_path)
        try:
            from agent.planning import tools as planning_tools
            await planning_tools.create_goal("长期目标", steps="x|y")

            plan_id = await tracker.submit_plan(
                SCOPE, "会话计划", tracker.parse_steps("a"),
            )
            await tracker.finalize_plan(SCOPE, "completed")

            # 计划已收敛，且不因目标存在而被 get_active_plan 误复用
            assert await tracker.get_active_plan(SCOPE) is None
            _, plan = await tracker.find_goal_by_id(plan_id)
            assert plan["status"] == "completed"
        finally:
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
            tracker.planning_store_port.set(store)
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
            tracker.planning_store_port.unbind()
            await _teardown(store)

    async def test_reflect_scope_prefix_not_user_facing(self) -> None:
        """reflect:<id> 唯一 scope 同样视为非用户会话（不发射前端事件）。"""
        from agent.planning.tracker import _is_user_facing
        assert not _is_user_facing("reflect")
        assert not _is_user_facing("reflect:abc12345")
        assert _is_user_facing("user_webui:u1")


class TestPersistDebounce:
    async def test_persist_skips_when_only_timestamp_changes(self, tmp_path):
        """_persist 去抖：语义内容未变（仅 updated_at 漂移）时不落库。"""
        store, tracker = await _setup(tmp_path)
        try:
            plan_id = await tracker.submit_plan(SCOPE, "目标", tracker.parse_steps("a"))
            entry, goal = await tracker.find_goal_by_id(plan_id)
            assert entry is not None and goal is not None
            version_before = entry.version

            await tracker._persist(entry, goal)

            reloaded, _ = await tracker.find_goal_by_id(plan_id)
            assert reloaded is not None
            assert reloaded.version == version_before
        finally:
            await _teardown(store)

    async def test_persist_writes_on_real_change(self, tmp_path):
        """_persist 真实变更：步骤状态推进正常落库并刷新 updated_at。"""
        store, tracker = await _setup(tmp_path)
        try:
            plan_id = await tracker.submit_plan(SCOPE, "目标", tracker.parse_steps("a|b"))
            entry, goal = await tracker.find_goal_by_id(plan_id)
            assert entry is not None and goal is not None
            version_before = entry.version

            goal["steps"][0]["status"] = "completed"
            await tracker._persist(entry, goal)

            reloaded, reloaded_goal = await tracker.find_goal_by_id(plan_id)
            assert reloaded is not None and reloaded_goal is not None
            assert reloaded.version == version_before + 1
            assert reloaded_goal["steps"][0]["status"] == "completed"
        finally:
            await _teardown(store)
