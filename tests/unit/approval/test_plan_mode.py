"""B5 自发 Plan 模式测试：present_plan 工具（新范式：直接执行 + SSE 公告，不走 ApprovalGate）。

新语义：
- present_plan 不再触发 ApprovalGate，调用后立即返回 ok + plan_id + status='executing'
- 同时 emit EVENT_PLAN_SUBMITTED 事件（scope / chat_id / plan_id / goal / steps / files / risks）
- update_goal 成功后 emit EVENT_PLAN_STEP_UPDATED / EVENT_PLAN_STATUS_CHANGED 事件
"""

from __future__ import annotations

import asyncio
import json

from core.event_bus import (
    EVENT_PLAN_STATUS_CHANGED,
    EVENT_PLAN_STEP_UPDATED,
    EVENT_PLAN_SUBMITTED,
    event_bus,
)


class TestPresentPlanTool:
    async def test_tool_returns_executing(self):
        """present_plan 调用后立即返回 executing 状态（无需批准）。"""
        from agent.planning.tools import present_plan
        out = json.loads(await present_plan(goal="目标", steps="1.a\n2.b"))
        assert out["ok"] is True
        assert out["status"] == "executing"
        assert "plan_id" in out

    async def test_tool_emits_plan_submitted_event(self):
        """present_plan 同步发射 plan_submitted 事件（含 scope/plan_id/goal/steps/files/risks）。"""
        from agent.planning.tools import present_plan

        captured: list[dict] = []

        async def _capture(payload):
            captured.append(payload)

        event_bus.on(EVENT_PLAN_SUBMITTED, _capture, owner="test.plan")
        try:
            out = json.loads(await present_plan(
                goal="重构 auth 模块",
                steps="1.梳理\n2.设计\n3.实现",
                files="a.py,b.py",
                risks="需重启",
            ))
            plan_id = out["plan_id"]

            # 事件可能在 asyncio task 中异步发射，等待一拍
            await asyncio.sleep(0.05)

            assert len(captured) == 1
            evt = captured[0]
            assert evt["plan_id"] == plan_id
            assert evt["goal"] == "重构 auth 模块"
            assert evt["files"] == "a.py,b.py"
            assert evt["risks"] == "需重启"
            assert len(evt["steps"]) == 3
            assert evt["steps"][0]["content"] == "1.梳理"
            # 程序级自动推进：第 1 步立即标记为 in_progress
            assert evt["steps"][0]["status"] == "in_progress"
            assert evt["steps"][1]["status"] == "pending"
        finally:
            event_bus.off_by_owner("test.plan")

    async def test_tool_parses_pipe_separated_steps(self):
        """steps 同时支持 \\n 与 | 分隔。"""
        from agent.planning.tools import present_plan

        captured: list[dict] = []

        async def _capture(payload):
            captured.append(payload)

        event_bus.on(EVENT_PLAN_SUBMITTED, _capture, owner="test.plan")
        try:
            await present_plan(goal="x", steps="a|b|c")
            await asyncio.sleep(0.05)
            assert len(captured[0]["steps"]) == 3
        finally:
            event_bus.off_by_owner("test.plan")


class TestUpdateGoal:
    async def test_emits_step_updated(self, tmp_path):
        """update_goal 成功后发射 plan_step_updated 事件。"""
        from agent.memory.memory_store import MemoryStore
        from agent.planning.tracker import planning_store_port

        # 注入临时 store（走正式初始化路径，同时绑定 tracker）
        store = MemoryStore(db_path=str(tmp_path / "mem.db"))
        planning_store_port.set(store)

        from agent.planning.tools import create_goal, update_goal

        # 创建一个 goal（复用 create_goal 路径）
        create_out = json.loads(await create_goal(title="测试", steps="1.a|2.b"))
        goal_id = create_out["goal"]["goal_id"]

        captured: list[dict] = []

        async def _capture(payload):
            captured.append(payload)

        event_bus.on(EVENT_PLAN_STEP_UPDATED, _capture, owner="test.plan")
        try:
            out = json.loads(await update_goal(
                goal_id=goal_id,
                step_index=0,
                step_status="in_progress",
                note="开始",
            ))
            assert out["success"] is True
            await asyncio.sleep(0.05)
            assert len(captured) == 1
            evt = captured[0]
            assert evt["plan_id"] == goal_id
            assert evt["step_index"] == 0
            assert evt["step_status"] == "in_progress"
            assert evt["note"] == "开始"
        finally:
            event_bus.off_by_owner("test.plan")
            await store.close()
            planning_store_port.unbind()

    async def test_emits_status_changed_on_completed(self, tmp_path):
        """update_goal 设置 goal_status='completed' 时发射 plan_status_changed。"""
        from agent.memory.memory_store import MemoryStore
        from agent.planning.tracker import planning_store_port

        store = MemoryStore(db_path=str(tmp_path / "mem.db"))
        planning_store_port.set(store)

        from agent.planning.tools import create_goal, update_goal

        create_out = json.loads(await create_goal(title="x", steps="a"))
        goal_id = create_out["goal"]["goal_id"]

        captured: list[dict] = []

        async def _capture(payload):
            captured.append(payload)

        event_bus.on(EVENT_PLAN_STATUS_CHANGED, _capture, owner="test.plan")
        try:
            await update_goal(goal_id=goal_id, goal_status="completed")
            await asyncio.sleep(0.05)
            assert len(captured) == 1
            assert captured[0]["goal_status"] == "completed"
            assert captured[0]["plan_id"] == goal_id
        finally:
            event_bus.off_by_owner("test.plan")
            await store.close()
            planning_store_port.unbind()


class TestApprovalGateNoLongerAsksForPlan:
    async def test_default_rules_does_not_include_present_plan(self):
        """新范式下 default_rules 不再包含 present_plan 的 ask 规则。"""
        from agent.approval.rules import default_rules
        rs = default_rules()
        patterns = [r.pattern for r in rs.rules]
        assert "present_plan" not in patterns
