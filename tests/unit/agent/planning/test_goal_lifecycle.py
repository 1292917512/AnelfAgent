"""目标生命周期收口测试：终态即清 / 停滞事实概况 / 删除事件同步。

回归诉求：目标完成后依赖 AI 自觉调用 delete_goal，遗忘即积压
（2026-09 单日 19 个残留）。语义：update_goal 终态即删（AI 显式操作的
直接收尾）；长期目标存续由 AI 全权管理——系统不按时间自动删除，
停滞只经心跳事实行呈现给 AI 决策。
"""

from __future__ import annotations

import json
import time

import pytest

from core.event_bus import EVENT_PLAN_DELETED, event_bus

SCOPE = "user_webui:test#chat1"


async def _setup(tmp_path):
    from agent.memory.memory_store import MemoryStore
    from agent.planning import situation, tracker

    store = MemoryStore(db_path=str(tmp_path / "mem.db"))
    tracker.planning_store_port.set(store)
    situation.reset()
    return store, tracker


async def _teardown(store) -> None:
    from agent.planning import situation, tracker

    await store.close()
    tracker.planning_store_port.unbind()
    situation.reset()


async def _age_goal(tracker, store, goal_id: str, days: float) -> None:
    """把目标 updated_at 回拨指定天数后写回存储。"""
    entry, goal = await tracker.find_goal_by_id(goal_id)
    assert entry is not None and goal is not None
    goal["updated_at"] = time.strftime(
        "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400),
    )
    entry.content = json.dumps(goal, ensure_ascii=False)
    await store.update(entry)


class TestTerminalCleanup:
    async def test_completed_deletes_goal(self, tmp_path):
        """终态即清：update_goal 标 completed 后条目删除 + 前端卡片移除事件。"""
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        captured: list[dict] = []

        async def _capture(payload):
            captured.append(payload)

        event_bus.on(EVENT_PLAN_DELETED, _capture)
        try:
            created = json.loads(await tools.create_goal("临时目标"))
            goal_id = created["goal"]["goal_id"]

            result = json.loads(await tools.update_goal(goal_id, goal_status="completed"))
            assert result["success"] is True
            assert "自动清理" in result["message"]

            assert await tracker.find_goal_by_id(goal_id) == (None, None)
            assert any(p.get("plan_id") == goal_id for p in captured)
            audit = await store.list_audit(memory_id=created["goal"]["memory_id"])
            deletes = [row for row in audit if row["action"] == "delete"]
            assert len(deletes) == 1
            assert deletes[0]["actor"] == "planning"
            assert deletes[0]["data"] == {
                "goal_id": goal_id, "title": "临时目标", "status": "completed", "destination": "deleted",
            }
            assert goal_id in deletes[0]["detail"]
            assert result["memory_id"] == created["goal"]["memory_id"]
            assert await store.count() == 0
        finally:
            event_bus.off(EVENT_PLAN_DELETED, _capture)
            await _teardown(store)

    async def test_cancelled_deletes_goal(self, tmp_path):
        """cancelled 同样即清。"""
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        try:
            created = json.loads(await tools.create_goal("废弃目标"))
            goal_id = created["goal"]["goal_id"]
            await tools.update_goal(goal_id, goal_status="cancelled")
            assert await tracker.find_goal_by_id(goal_id) == (None, None)
        finally:
            await _teardown(store)

    async def test_recurring_completed_resets(self, tmp_path):
        """循环目标完成即重置：步骤归零、恢复 active，不删除。"""
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        try:
            created = json.loads(await tools.create_goal("循环", steps="a|b", recurring=True))
            goal_id = created["goal"]["goal_id"]
            await tools.update_goal(goal_id, step_index=0, step_status="completed")
            await tools.update_goal(goal_id, goal_status="completed")
            _, goal = await tracker.find_goal_by_id(goal_id)
            assert goal is not None
            assert goal["status"] == "active"
            assert all(s["status"] == "pending" for s in goal["steps"])
            assert not any(row["action"] == "delete" for row in await store.list_audit())
        finally:
            await _teardown(store)

    async def test_invalid_goal_status_rejected(self, tmp_path):
        """非法整体状态返回 PARAM 错误，不静默生效。"""
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        try:
            created = json.loads(await tools.create_goal("目标"))
            goal_id = created["goal"]["goal_id"]
            result = json.loads(await tools.update_goal(goal_id, goal_status="finished"))
            assert result.get("cause") == "param"
            _, goal = await tracker.find_goal_by_id(goal_id)
            assert goal is not None and goal["status"] == "active"
        finally:
            await _teardown(store)


class TestAllStepsAutoClose:
    async def test_last_step_completion_autocloses(self, tmp_path):
        """全部步骤标记完成后目标自动收口删除（完成事实驱动的关闭）。"""
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        try:
            goal_id = json.loads(await tools.create_goal("三步目标", steps="a|b|c"))["goal"]["goal_id"]
            await tools.update_goal(goal_id, step_index=0, step_status="completed")
            await tools.update_goal(goal_id, step_index=1, step_status="completed")

            result = json.loads(
                await tools.update_goal(goal_id, step_index=2, step_status="completed")
            )
            assert result["success"] is True
            assert "自动收口" in result["message"]
            assert await tracker.find_goal_by_id(goal_id) == (None, None)
            audit = await store.list_audit(memory_id=result["memory_id"])
            assert audit[0]["data"]["status"] == "completed"
        finally:
            await _teardown(store)

    async def test_partial_progress_keeps_goal(self, tmp_path):
        """步骤未全部完成时不收口。"""
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        try:
            goal_id = json.loads(await tools.create_goal("目标", steps="a|b"))["goal"]["goal_id"]
            await tools.update_goal(goal_id, step_index=0, step_status="completed")
            _, goal = await tracker.find_goal_by_id(goal_id)
            assert goal is not None and goal["status"] == "active"
        finally:
            await _teardown(store)

    async def test_skipped_steps_count_as_done(self, tmp_path):
        """skipped 是终态：全部步骤 done（含 skipped）即收口。"""
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        try:
            goal_id = json.loads(await tools.create_goal("目标", steps="a|b"))["goal"]["goal_id"]
            await tools.update_goal(goal_id, step_index=0, step_status="completed")
            result = json.loads(
                await tools.update_goal(goal_id, step_index=1, step_status="skipped")
            )
            assert "自动收口" in result["message"]
            assert await tracker.find_goal_by_id(goal_id) == (None, None)
        finally:
            await _teardown(store)

    async def test_text_update_never_autocloses(self, tmp_path):
        """纯文本/备注更新不触发收口：关闭只因步骤推进的事实。"""
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        try:
            goal_id = json.loads(await tools.create_goal("目标", steps="a"))["goal"]["goal_id"]
            entry, goal = await tracker.find_goal_by_id(goal_id)
            assert entry is not None and goal is not None
            goal["steps"][0]["status"] = "completed"
            entry.content = json.dumps(goal, ensure_ascii=False)
            await store.update(entry)

            result = json.loads(await tools.update_goal(goal_id, title="新标题"))
            assert result["success"] is True
            _, goal = await tracker.find_goal_by_id(goal_id)
            assert goal is not None and goal["title"] == "新标题"
        finally:
            await _teardown(store)

    async def test_recurring_not_autoclosed_by_steps(self, tmp_path):
        """循环目标步骤全完成不收口：跨周期存续，等显式标记重置。"""
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        try:
            goal_id = json.loads(
                await tools.create_goal("循环", steps="a", recurring=True)
            )["goal"]["goal_id"]
            await tools.update_goal(goal_id, step_index=0, step_status="completed")
            _, goal = await tracker.find_goal_by_id(goal_id)
            assert goal is not None and goal["status"] == "active"
        finally:
            await _teardown(store)


class TestGoalIntegrity:
    async def test_archived_goal_can_be_inspected_but_not_mutated(self, tmp_path):
        from agent.memory.memory_types import MemoryEntry, MemoryType
        from agent.planning import situation, tools

        store, tracker = await _setup(tmp_path)
        try:
            goal = {"goal_id": "legacy", "title": "历史目标", "status": "active", "steps": []}
            mid = await store.add(MemoryEntry(
                content=json.dumps(goal), memory_type=MemoryType.SEMANTIC, tags=["goal:legacy"],
            ))
            assert await store.archive_memory(mid, reason="old governance")
            # 模拟保护机制启用前误归档的规划载体。
            db = await store._get_db()
            async with store._tx(db):
                await db.execute("UPDATE memories_archive SET source='goal' WHERE id=?", (mid,))
            assert await tracker.find_goal_by_id("legacy") == (None, None)
            result = json.loads(await tools.get_goal("legacy"))
            assert result["archived"] and result["goal"]["memory_id"] == mid
            assert result["archive_reason"] == "old governance"
            assert json.loads(await tools.list_goals())["total"] == 0
            assert json.loads(await tools.list_goals(include_archived=True))["goals"][0]["archived"]
            assert (await situation.active_goal_briefs()) == []
            rejected = json.loads(await tools.update_goal("legacy", goal_status="completed"))
            assert rejected["cause"] == "state" and rejected["memory_id"] == mid
            assert json.loads(await tools.delete_goal("legacy"))["cause"] == "state"
            linked_id = await store.add(MemoryEntry(
                content="历史目标的依据", memory_type=MemoryType.SEMANTIC, tags=["goal:legacy"],
            ))
            assert not await store.archive_memory(linked_id)
            assert await store.restore_memory(mid)
            assert json.loads(await tools.update_goal("legacy", goal_status="completed"))["success"]
            assert await store.archive_memory(linked_id)
        finally:
            await _teardown(store)

    @pytest.mark.parametrize("by_steps", [False, True])
    async def test_concurrent_change_prevents_terminal_cleanup(self, tmp_path, monkeypatch, by_steps):
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        captured: list[dict] = []

        async def capture(payload):
            captured.append(payload)

        event_bus.on(EVENT_PLAN_DELETED, capture)
        try:
            created = json.loads(await tools.create_goal("目标", steps="a"))["goal"]
            original_find = tools._find_goal

            async def concurrent_find(goal_id):
                entry, goal = await original_find(goal_id)
                latest = entry.model_copy(deep=True)
                latest.content = latest.content.replace("目标", "更新后的目标")
                assert await store.update(latest)
                return entry, goal

            monkeypatch.setattr(tools, "_find_goal", concurrent_find)
            result = json.loads(await tools.update_goal(
                created["goal_id"], **({"step_index": 0, "step_status": "completed"} if by_steps else {"goal_status": "completed"}),
            ))
            assert result["cause"] == "state"
            assert not captured
            assert "更新后的目标" in (await store.get(created["memory_id"])).content
            assert not any(row["action"] == "delete" for row in await store.list_audit())
        finally:
            event_bus.off(EVENT_PLAN_DELETED, capture)
            await _teardown(store)

    async def test_required_audit_failure_rolls_back_goal_deletion(self, tmp_path):
        from agent.planning import tools

        store, tracker = await _setup(tmp_path)
        try:
            goal = json.loads(await tools.create_goal("必须留痕"))["goal"]
            entry, data = await tracker.find_goal_by_id(goal["goal_id"])
            db = await store._get_db()
            async with store._tx(db):
                await db.execute(
                    "CREATE TRIGGER reject_delete_audit BEFORE INSERT ON memory_audit "
                    "WHEN NEW.action='delete' BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END",
                )
            with pytest.raises(Exception, match="audit unavailable"):
                await tracker.remove_goal(entry, goal=data, status="completed")
            assert await store.get(goal["memory_id"]) is not None
            assert not any(row["action"] == "delete" for row in await store.list_audit())
        finally:
            await _teardown(store)


class TestStaleFactLine:
    async def test_stale_line_reports_without_deleting(self, tmp_path):
        """停滞概况只呈现事实：列出停滞目标，条目全部保留（决策归 AI）。"""
        store, tracker = await _setup(tmp_path)
        try:
            from agent.planning import situation, tools

            fresh = json.loads(await tools.create_goal("近期目标"))["goal"]["goal_id"]
            stale = json.loads(await tools.create_goal("长期目标"))["goal"]["goal_id"]
            await _age_goal(tracker, store, stale, days=30)

            line = await situation.stale_goal_line()
            assert stale in line and "(30天)" in line
            assert "系统不自动清理" in line
            assert fresh not in line

            # 事实行不做任何处置：两条目标都还在
            assert await tracker.find_goal_by_id(stale) != (None, None)
            assert await tracker.find_goal_by_id(fresh) != (None, None)
        finally:
            await _teardown(store)

    async def test_stale_line_empty_when_fresh(self, tmp_path):
        """无停滞目标时概况行为空（心跳日志不注入）。"""
        store, tracker = await _setup(tmp_path)
        try:
            from agent.planning import situation, tools

            await tools.create_goal("近期目标")
            assert await situation.stale_goal_line() == ""
        finally:
            await _teardown(store)

    async def test_stale_line_caps_items(self, tmp_path):
        """停滞目标超过 5 个时只列前 5 + 总数，行保持有界。"""
        store, tracker = await _setup(tmp_path)
        try:
            from agent.planning import situation, tools

            for i in range(7):
                goal_id = json.loads(await tools.create_goal(f"目标{i}"))["goal"]["goal_id"]
                await _age_goal(tracker, store, goal_id, days=10 + i)

            line = await situation.stale_goal_line()
            assert "等 7 个" in line
            assert line.count("天)") == 5
        finally:
            await _teardown(store)
