"""短期记忆溢出晋升（WorkMemory.add_temporary 截尾 → events 日期便签）单元测试。"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.memory import notes
from agent.mind.work_memory import WorkMemory


@pytest.fixture
def work_memory(tmp_path, monkeypatch) -> WorkMemory:
    """容量为 2 的短期记忆 + 隔离的便签工作区。"""
    ws = tmp_path / "config"
    (ws / "memory" / "events").mkdir(parents=True)
    monkeypatch.setattr(notes, "_workspace_dir", ws)
    monkeypatch.setattr(WorkMemory, "_max_temp", property(lambda self: 2))
    return WorkMemory(everything_data=SimpleNamespace())


def test_overflow_promoted_to_events_note(work_memory: WorkMemory, tmp_path: Path) -> None:
    work_memory.add_temporary({"role": "user", "content": "第一条"})
    work_memory.add_temporary({"role": "user", "content": "第二条"})
    work_memory.add_temporary({"role": "user", "content": "第三条"})

    # 桶内只留最新 2 条
    bucket = work_memory.get_temporary()
    assert [c["content"] for c in bucket] == ["第二条", "第三条"]

    # 被挤出的「第一条」晋升到当天 events 便签
    today = time.strftime("%Y-%m-%d")
    note = tmp_path / "config" / "memory" / "events" / f"{today}.md"
    assert note.exists()
    text = note.read_text(encoding="utf-8")
    assert "第一条" in text
    assert "短期记忆溢出" in text


def test_no_overflow_no_note(work_memory: WorkMemory, tmp_path: Path) -> None:
    work_memory.add_temporary({"role": "user", "content": "唯一一条"})
    today = time.strftime("%Y-%m-%d")
    note = tmp_path / "config" / "memory" / "events" / f"{today}.md"
    assert not note.exists()


def test_scoped_buckets_overflow_independently(work_memory: WorkMemory, tmp_path: Path) -> None:
    for i in range(3):
        work_memory.add_temporary({"role": "user", "content": f"群消息{i}"}, scope="group_qq:1")
    assert [c["content"] for c in work_memory.get_temporary("group_qq:1") if c["content"].startswith("群消息")] == ["群消息1", "群消息2"]
    today = time.strftime("%Y-%m-%d")
    text = (tmp_path / "config" / "memory" / "events" / f"{today}.md").read_text(encoding="utf-8")
    assert "群消息0" in text
    assert "group_qq:1" in text
