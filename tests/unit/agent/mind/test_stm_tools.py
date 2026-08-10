"""短期记忆管理（WorkMemory scope 视图删除/清空 + short_term_tools 工具）单元测试。

回归场景：短期记忆是任务提醒的投递区，但此前只有容量溢出与 WebUI 管理两条移除路径，
AI 无法自清理，导致已完成的提醒不断堆积、持续占用上下文。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.mind.tools import short_term_tools
from agent.mind.work_memory import WorkMemory


@pytest.fixture
def work_memory() -> WorkMemory:
    wm = WorkMemory(everything_data=SimpleNamespace())
    wm.add_temporary({"role": "system", "content": "全局推送"}, scope="")
    wm.add_temporary({"role": "user", "content": "任务提醒A"}, scope="group_qq:1")
    wm.add_temporary({"role": "user", "content": "任务提醒B"}, scope="group_qq:1")
    wm.add_temporary({"role": "user", "content": "别的群提醒"}, scope="group_qq:2")
    return wm


class TestScopeViewOps:
    def test_view_order_default_bucket_first(self, work_memory: WorkMemory) -> None:
        view = work_memory.get_temporary("group_qq:1")
        assert [c["content"] for c in view] == ["全局推送", "任务提醒A", "任务提醒B"]

    def test_delete_in_scope_default_bucket(self, work_memory: WorkMemory) -> None:
        assert work_memory.delete_temporary_in_scope("group_qq:1", 0)
        assert [c["content"] for c in work_memory.get_temporary("group_qq:1")] == ["任务提醒A", "任务提醒B"]

    def test_delete_in_scope_scope_bucket(self, work_memory: WorkMemory) -> None:
        assert work_memory.delete_temporary_in_scope("group_qq:1", 2)
        assert [c["content"] for c in work_memory.get_temporary("group_qq:1")] == ["全局推送", "任务提醒A"]
        # 其他 scope 桶不受影响
        assert [c["content"] for c in work_memory.get_temporary("group_qq:2")] == ["全局推送", "别的群提醒"]

    def test_delete_in_scope_out_of_range(self, work_memory: WorkMemory) -> None:
        assert not work_memory.delete_temporary_in_scope("group_qq:1", 3)
        assert not work_memory.delete_temporary_in_scope("group_qq:1", -1)
        assert len(work_memory.get_temporary("group_qq:1")) == 3

    def test_clear_in_scope(self, work_memory: WorkMemory) -> None:
        assert work_memory.clear_temporary_in_scope("group_qq:1") == 3
        assert work_memory.get_temporary("group_qq:1") == []
        # 其他 scope 桶保留
        assert [c["content"] for c in work_memory.get_temporary("group_qq:2")] == ["别的群提醒"]


@pytest.fixture
def tools(work_memory: WorkMemory, monkeypatch):
    """注入 PFC 引用并固定当前 scope。"""
    monkeypatch.setattr(
        short_term_tools,
        "_pfc_ref",
        SimpleNamespace(
            get_temporary=work_memory.get_temporary,
            delete_temporary_in_scope=work_memory.delete_temporary_in_scope,
            clear_temporary_in_scope=work_memory.clear_temporary_in_scope,
        ),
    )
    monkeypatch.setattr(short_term_tools, "_current_scope", lambda: "group_qq:1")
    return short_term_tools


class TestShortTermTools:
    async def test_list_with_indexes(self, tools) -> None:
        data = json.loads(await tools.list_short_term_memory())
        assert data["count"] == 3
        assert [i["content"] for i in data["items"]] == ["全局推送", "任务提醒A", "任务提醒B"]
        assert [i["index"] for i in data["items"]] == [0, 1, 2]

    async def test_remove_by_index(self, tools, work_memory: WorkMemory) -> None:
        data = json.loads(await tools.remove_short_term_memory(1))
        assert data == {"removed": 1, "remaining": 2}
        assert [c["content"] for c in work_memory.get_temporary("group_qq:1")] == ["全局推送", "任务提醒B"]

    async def test_remove_invalid_index(self, tools) -> None:
        result = json.loads(await tools.remove_short_term_memory(9))
        assert "error" in result

    async def test_clear(self, tools, work_memory: WorkMemory) -> None:
        data = json.loads(await tools.clear_short_term_memory())
        assert data["cleared"] == 3
        assert work_memory.get_temporary("group_qq:1") == []
        assert [c["content"] for c in work_memory.get_temporary("group_qq:2")] == ["别的群提醒"]
