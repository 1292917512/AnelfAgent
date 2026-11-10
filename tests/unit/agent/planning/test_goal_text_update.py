"""update_goal 文本更新测试（标题/描述原地改写，goal_id 与标签串联不变）。

回归诉求：目标文本此前只能删了重建——goal_id 变化导致 goal:{id} 记忆
串联断裂，AI 面对「更新目标描述」无正路可走。
"""

from __future__ import annotations

import json

import pytest
from helpers.planning import update_goal

from core.config import ConfigManager


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
    ConfigManager._config.pop("goal_stale_days", None)


class TestGoalTextUpdate:
    @pytest.mark.parametrize("selector", [{"step_index": 0}, {"step_index": 0, "step_name": "发布"},
                                         {"step_name": "重复"}])
    async def test_unsafe_step_selector_does_not_write(self, tmp_path, selector):
        from agent.planning import tools

        store, _ = await _setup(tmp_path)
        try:
            goal = json.loads(await tools.create_goal("安全定位", steps="准备|发布|重复|重复"))["goal"]
            result = json.loads(await tools.update_goal(
                goal["goal_id"], expected_version=goal["version"], note="不应写入", **selector,
            ))
            assert "error" in result
            assert json.loads(await tools.get_goal(goal["goal_id"]))["goal"] == goal
        finally:
            await _teardown(store)

    async def test_version_identity_append_and_terminal_receipt(self, tmp_path):
        from agent.planning import goals, tools

        store, _ = await _setup(tmp_path)
        try:
            goal = json.loads(await tools.create_goal("发布", steps="准备|发布"))["goal"]
            gid = goal["goal_id"]
            sid = goal["steps"][0]["step_id"]
            assert "error" in json.loads(await tools.update_goal(gid, title="没有版本"))
            entry, original = await goals.find_goal(store, gid)
            reordered = await goals.update_goal(store, entry, original, expected_version=goal["version"],
                                                 steps=list(reversed(goal["steps"])))
            assert "error" in json.loads(await tools.update_goal(
                gid, expected_version=goal["version"], step_id=sid, note="旧快照",
            ))
            first = json.loads(await tools.update_goal(
                gid, expected_version=reordered["version"], step_id=sid, note="第一条",
            ))["goal"]
            assert first["steps"][1]["note"] == "第一条"
            result = json.loads(await tools.update_goal(
                gid, expected_version=first["version"], step_id=sid, note="最终验证", note_mode="append",
                goal_status="completed",
            ))
            assert result["goal"]["deleted"] is True
            receipt = json.loads(await tools.get_goal(gid))["goal"]
            assert receipt["deleted"] is True
            assert receipt["steps"][1]["step_id"] == sid
            assert receipt["steps"][1]["note"] == "第一条\n最终验证"
            assert await goals.find_goal(store, gid) == (None, None)
        finally:
            await _teardown(store)

    async def test_active_goal_is_not_hidden_behind_history(self, tmp_path):
        from agent.memory.memory_types import GOAL_SOURCE, MemoryEntry, MemoryType
        from agent.planning import tools

        store, _ = await _setup(tmp_path)
        try:
            goal = json.loads(await tools.create_goal("仍需处理"))["goal"]
            for i in range(105):
                await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, source=GOAL_SOURCE,
                    content=json.dumps({"goal_id": f"history{i}", "title": "历史", "status": "completed"})))
            listed = json.loads(await tools.list_goals(status="active"))
            assert [g["goal_id"] for g in listed["goals"]] == [goal["goal_id"]]
        finally:
            await _teardown(store)

    async def test_update_title_and_description(self, tmp_path):
        store, tracker = await _setup(tmp_path)
        try:
            from agent.planning import tools

            goal = json.loads(await tools.create_goal("旧标题", description="旧描述"))["goal"]
            updated = json.loads(await update_goal(
                goal["goal_id"], title="新标题", description="新描述",
            ))
            assert updated["success"] is True
            assert updated["goal"]["title"] == "新标题"
            assert updated["goal"]["description"] == "新描述"
            # goal_id 不变：goal:{id} 记忆串联与外部引用继续有效
            assert updated["goal"]["goal_id"] == goal["goal_id"]

            entry, persisted = await tracker.find_goal_by_id(goal["goal_id"])
            assert entry is not None and persisted is not None
            assert persisted["title"] == "新标题"
            assert persisted["description"] == "新描述"
        finally:
            await _teardown(store)

    async def test_clear_description_token(self, tmp_path):
        store, _tracker = await _setup(tmp_path)
        try:
            from agent.planning import tools

            goal = json.loads(await tools.create_goal("标题", description="旧描述"))["goal"]
            updated = json.loads(await update_goal(goal["goal_id"], description="clear"))
            assert updated["goal"]["description"] == ""
        finally:
            await _teardown(store)

    async def test_empty_params_rejected(self, tmp_path):
        store, _tracker = await _setup(tmp_path)
        try:
            from agent.planning import tools

            goal = json.loads(await tools.create_goal("标题"))["goal"]
            result = json.loads(await update_goal(goal["goal_id"]))
            assert "error" in result
            assert "没有任何字段需要更新" in result["error"]
        finally:
            await _teardown(store)

    async def test_text_update_keeps_goal_active(self, tmp_path):
        """纯文本更新不改生命周期：目标保持 active、不触发终态清理。"""
        store, tracker = await _setup(tmp_path)
        try:
            from agent.planning import tools

            goal = json.loads(await tools.create_goal("旧标题"))["goal"]
            updated = json.loads(await update_goal(goal["goal_id"], title="改名"))
            assert updated["goal"]["status"] == "active"
            entry, persisted = await tracker.find_goal_by_id(goal["goal_id"])
            assert entry is not None and persisted is not None
            assert persisted["title"] == "改名"
        finally:
            await _teardown(store)
