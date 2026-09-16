"""update_goal 文本更新测试（标题/描述原地改写，goal_id 与标签串联不变）。

回归诉求：目标文本此前只能删了重建——goal_id 变化导致 goal:{id} 记忆
串联断裂，AI 面对「更新目标描述」无正路可走。
"""

from __future__ import annotations

import json

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
    async def test_update_title_and_description(self, tmp_path):
        store, tracker = await _setup(tmp_path)
        try:
            from agent.planning import tools

            goal = json.loads(await tools.create_goal("旧标题", description="旧描述"))["goal"]
            updated = json.loads(await tools.update_goal(
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
            updated = json.loads(await tools.update_goal(goal["goal_id"], description="clear"))
            assert updated["goal"]["description"] == ""
        finally:
            await _teardown(store)

    async def test_empty_params_rejected(self, tmp_path):
        store, _tracker = await _setup(tmp_path)
        try:
            from agent.planning import tools

            goal = json.loads(await tools.create_goal("标题"))["goal"]
            result = json.loads(await tools.update_goal(goal["goal_id"]))
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
            updated = json.loads(await tools.update_goal(goal["goal_id"], title="改名"))
            assert updated["goal"]["status"] == "active"
            entry, persisted = await tracker.find_goal_by_id(goal["goal_id"])
            assert entry is not None and persisted is not None
            assert persisted["title"] == "改名"
        finally:
            await _teardown(store)
