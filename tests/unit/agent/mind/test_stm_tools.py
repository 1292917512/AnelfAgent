"""短期记忆（PFC）单元测试：scope 视图删除/清空 + 自管理工具 + 溢出晋升。

回归场景：短期记忆是任务提醒的投递区，但此前只有容量溢出与 WebUI 管理两条移除路径，
AI 无法自清理，导致已完成的提醒不断堆积、持续占用上下文。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.memory import notes
from agent.mind.pfc import PrefrontalCortex
from agent.mind.tools import short_term_tools
from agent.mind.tools.ports import mind_port


@pytest.fixture
def pfc() -> PrefrontalCortex:
    wm = PrefrontalCortex(everything_data=SimpleNamespace())
    wm.add_temporary({"role": "system", "content": "全局推送"}, scope="")
    wm.add_temporary({"role": "user", "content": "任务提醒A"}, scope="group_qq:1")
    wm.add_temporary({"role": "user", "content": "任务提醒B"}, scope="group_qq:1")
    wm.add_temporary({"role": "user", "content": "别的群提醒"}, scope="group_qq:2")
    return wm


class TestScopeViewOps:
    def test_view_order_default_bucket_first(self, pfc: PrefrontalCortex) -> None:
        view = pfc.get_temporary("group_qq:1")
        assert [c["content"] for c in view] == ["全局推送", "任务提醒A", "任务提醒B"]

    def test_delete_in_scope_default_bucket(self, pfc: PrefrontalCortex) -> None:
        assert pfc.delete_temporary_in_scope("group_qq:1", 0)
        assert [c["content"] for c in pfc.get_temporary("group_qq:1")] == ["任务提醒A", "任务提醒B"]

    def test_delete_in_scope_scope_bucket(self, pfc: PrefrontalCortex) -> None:
        assert pfc.delete_temporary_in_scope("group_qq:1", 2)
        assert [c["content"] for c in pfc.get_temporary("group_qq:1")] == ["全局推送", "任务提醒A"]
        # 其他 scope 桶不受影响
        assert [c["content"] for c in pfc.get_temporary("group_qq:2")] == ["全局推送", "别的群提醒"]

    def test_delete_in_scope_out_of_range(self, pfc: PrefrontalCortex) -> None:
        assert not pfc.delete_temporary_in_scope("group_qq:1", 3)
        assert not pfc.delete_temporary_in_scope("group_qq:1", -1)
        assert len(pfc.get_temporary("group_qq:1")) == 3

    def test_clear_in_scope(self, pfc: PrefrontalCortex) -> None:
        assert pfc.clear_temporary_in_scope("group_qq:1") == 3
        assert pfc.get_temporary("group_qq:1") == []
        # 其他 scope 桶保留
        assert [c["content"] for c in pfc.get_temporary("group_qq:2")] == ["别的群提醒"]


@pytest.fixture
def tools(pfc: PrefrontalCortex, monkeypatch):
    """经 mind 端口注入 PFC 引用并固定当前 scope。"""
    mind_port.set(SimpleNamespace(pfc=SimpleNamespace(  # type: ignore[arg-type]
        get_temporary=pfc.get_temporary,
        delete_temporary_in_scope=pfc.delete_temporary_in_scope,
        clear_temporary_in_scope=pfc.clear_temporary_in_scope,
    )))
    monkeypatch.setattr(short_term_tools, "_current_scope", lambda: "group_qq:1")
    yield short_term_tools
    mind_port.unbind()


class TestShortTermTools:
    async def test_list_with_indexes(self, tools) -> None:
        data = json.loads(await tools.list_short_term_memory())
        assert data["count"] == 3
        assert [i["content"] for i in data["items"]] == ["全局推送", "任务提醒A", "任务提醒B"]
        assert [i["index"] for i in data["items"]] == [0, 1, 2]

    async def test_remove_by_index(self, tools, pfc: PrefrontalCortex) -> None:
        data = json.loads(await tools.remove_short_term_memory(1))
        assert data == {"removed": 1, "remaining": 2}
        assert [c["content"] for c in pfc.get_temporary("group_qq:1")] == ["全局推送", "任务提醒B"]

    async def test_remove_invalid_index(self, tools) -> None:
        result = json.loads(await tools.remove_short_term_memory(9))
        assert "error" in result

    async def test_clear(self, tools, pfc: PrefrontalCortex) -> None:
        data = json.loads(await tools.clear_short_term_memory())
        assert data["cleared"] == 3
        assert pfc.get_temporary("group_qq:1") == []
        assert [c["content"] for c in pfc.get_temporary("group_qq:2")] == ["别的群提醒"]


# ------------------------------------------------------------------
# 溢出晋升：容量截尾 → events 日期便签
# ------------------------------------------------------------------

@pytest.fixture
def capped_memory(tmp_path, monkeypatch) -> PrefrontalCortex:
    """容量为 2 的短期记忆 + 隔离的便签工作区。"""
    ws = tmp_path / "config"
    (ws / "memory" / "events").mkdir(parents=True)
    monkeypatch.setattr(notes, "_memory_dir", ws / "memory")
    monkeypatch.setattr(PrefrontalCortex, "max_temp", property(lambda self: 2))
    return PrefrontalCortex(everything_data=SimpleNamespace())


class TestOverflowPromotion:
    def test_overflow_promoted_to_events_note(self, capped_memory: PrefrontalCortex, tmp_path: Path) -> None:
        capped_memory.add_temporary({"role": "user", "content": "第一条"})
        capped_memory.add_temporary({"role": "user", "content": "第二条"})
        capped_memory.add_temporary({"role": "user", "content": "第三条"})

        # 桶内只留最新 2 条
        bucket = capped_memory.get_temporary()
        assert [c["content"] for c in bucket] == ["第二条", "第三条"]

        # 被挤出的「第一条」晋升到当天 events 便签
        today = time.strftime("%Y-%m-%d")
        note = tmp_path / "config" / "memory" / "events" / f"{today}.md"
        assert note.exists()
        text = note.read_text(encoding="utf-8")
        assert "第一条" in text
        assert "短期记忆溢出" in text

    def test_no_overflow_no_note(self, capped_memory: PrefrontalCortex, tmp_path: Path) -> None:
        capped_memory.add_temporary({"role": "user", "content": "唯一一条"})
        today = time.strftime("%Y-%m-%d")
        note = tmp_path / "config" / "memory" / "events" / f"{today}.md"
        assert not note.exists()

    def test_scoped_buckets_overflow_independently(self, capped_memory: PrefrontalCortex, tmp_path: Path) -> None:
        for i in range(3):
            capped_memory.add_temporary({"role": "user", "content": f"群消息{i}"}, scope="group_qq:1")
        assert [c["content"] for c in capped_memory.get_temporary("group_qq:1") if c["content"].startswith("群消息")] == ["群消息1", "群消息2"]
        today = time.strftime("%Y-%m-%d")
        text = (tmp_path / "config" / "memory" / "events" / f"{today}.md").read_text(encoding="utf-8")
        assert "群消息0" in text
        assert "group_qq:1" in text
