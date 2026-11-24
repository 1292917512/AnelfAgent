"""会话计划按确认的步骤归档，跨轮及中断保留尚未完成的计划。"""

from __future__ import annotations

import json

from helpers.planning import update_goal

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


async def test_step_writes_archive_once_and_preserve_other_goals(tmp_path):
    from agent.planning import tools

    store, tracker = await _setup(tmp_path)
    try:
        other = json.loads(await tools.create_goal("独立目标", steps="a|b"))["goal"]
        plan_id = await tracker.submit_plan(SCOPE, "会话计划", tracker.parse_steps("制作|核验"))
        partial = json.loads(await update_goal(plan_id, step_index=0, step_status="completed", note="已制作"))["goal"]
        assert [s["status"] for s in partial["steps"]] == ["completed", "pending"]
        await update_goal(plan_id, step_index=1, step_status="completed", note="已核验")
        archived = json.loads(await tools.get_goal(plan_id))["goal"]
        assert archived["archive_reason"] == "all_steps_completed"
        assert [s["note"] for s in archived["steps"]] == ["已制作", "已核验"]
        assert len([r for r in await store.list_audit(memory_id=archived["memory_id"]) if r["action"] == "archive"]) == 1
        assert json.loads(await tools.get_goal(other["goal_id"]))["goal"] == other
    finally:
        await _teardown(store)


class TestCancelPlan:
    async def test_explicit_cancel_archives_original_steps(self, tmp_path):
        store, tracker = await _setup(tmp_path)
        try:
            gid = await tracker.submit_plan(SCOPE, "目标", tracker.parse_steps("a|b"))
            active = await tracker.get_active_plan(SCOPE)
            assert await tracker.cancel_plan(SCOPE, gid)
            entry, archived = await tracker.find_goal_by_id(gid, include_archived=True)
            assert entry.archive_reason == "explicit_cancelled"
            assert archived["status"] == "cancelled" and archived["steps"] == active["steps"]
            assert await tracker.get_active_plan(SCOPE) is None
        finally:
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
            tracker.planning_store_port.set(store)
            plan_id = await tracker.submit_plan(
                SCOPE, "目标", tracker.parse_steps("a|b"),
            )
            await update_goal(plan_id, step_index=0, step_status="completed")
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
