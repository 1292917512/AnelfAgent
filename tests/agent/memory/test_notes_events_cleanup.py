"""events 日期便签生命周期（过期检测与删除）单元测试。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from agent.memory import notes


@pytest.fixture
def memory_dir(tmp_path, monkeypatch) -> Path:
    """隔离的记忆工作区。"""
    ws = tmp_path / "config"
    md = ws / "memory"
    (md / "events").mkdir(parents=True)
    monkeypatch.setattr(notes, "_workspace_dir", ws)
    return md


_TODAY = date(2026, 7, 20)


class TestListExpiredEvents:
    def test_empty_when_no_events_dir(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "config"
        (ws / "memory").mkdir(parents=True)
        monkeypatch.setattr(notes, "_workspace_dir", ws)
        assert notes.list_expired_events(30, today=_TODAY) == []

    def test_boundary_exactly_retention_days_kept(self, memory_dir: Path) -> None:
        events = memory_dir / "events"
        (events / "2026-06-20.md").write_text("# 恰好 30 天", encoding="utf-8")
        (events / "2026-06-19.md").write_text("# 31 天", encoding="utf-8")

        expired = notes.list_expired_events(30, today=_TODAY)
        dates = [e.date for e in expired]
        assert dates == ["2026-06-19"]

    def test_sorted_by_date_ascending(self, memory_dir: Path) -> None:
        events = memory_dir / "events"
        (events / "2026-05-10.md").write_text("# a", encoding="utf-8")
        (events / "2026-05-07.md").write_text("# b", encoding="utf-8")
        (events / "2026-07-19.md").write_text("# 未过期", encoding="utf-8")

        expired = notes.list_expired_events(30, today=_TODAY)
        assert [e.date for e in expired] == ["2026-05-07", "2026-05-10"]
        assert expired[0].path == "memory/events/2026-05-07.md"
        assert expired[0].abs_path == events / "2026-05-07.md"

    def test_non_date_files_ignored(self, memory_dir: Path) -> None:
        events = memory_dir / "events"
        (events / "archive.md").write_text("# 非日期", encoding="utf-8")
        (events / "2026-13-01.md").write_text("# 非法日期", encoding="utf-8")
        (events / "2026-05-07.md").write_text("# 正常", encoding="utf-8")

        expired = notes.list_expired_events(30, today=_TODAY)
        assert [e.date for e in expired] == ["2026-05-07"]


class TestDeleteExpiredEvent:
    def test_delete_via_existing_entry(self, memory_dir: Path) -> None:
        events = memory_dir / "events"
        target = events / "2026-05-07.md"
        target.write_text("# 旧事件", encoding="utf-8")

        expired = notes.list_expired_events(30, today=_TODAY)
        assert len(expired) == 1
        assert notes.delete_memory_file(expired[0].path) is True
        assert not target.exists()
        assert notes.list_expired_events(30, today=_TODAY) == []
