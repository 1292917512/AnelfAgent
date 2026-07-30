"""便签系统单元测试：目录迁移 / 智能截断 / events 日期便签生命周期。"""

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
    md.mkdir(parents=True)
    monkeypatch.setattr(notes, "_workspace_dir", ws)
    return md


@pytest.fixture
def events_dir(tmp_path, monkeypatch) -> Path:
    """隔离的记忆工作区（预建 events 目录）。"""
    ws = tmp_path / "config"
    md = ws / "memory"
    (md / "events").mkdir(parents=True)
    monkeypatch.setattr(notes, "_workspace_dir", ws)
    return md


# ==================================================================
# 目录迁移与智能加载
# ==================================================================

class TestMigrateMemoryLayout:
    def test_date_files_moved_to_events(self, memory_dir: Path) -> None:
        (memory_dir / "2026-05-07.md").write_text("# 事件", encoding="utf-8")
        (memory_dir / "2026-07-17.md").write_text("# 事件2", encoding="utf-8")
        (memory_dir / "memory.md").write_text("# 主便签", encoding="utf-8")

        moved = notes.migrate_memory_layout()
        assert len(moved) == 2
        assert (memory_dir / "events" / "2026-05-07.md").exists()
        assert (memory_dir / "events" / "2026-07-17.md").exists()
        assert not (memory_dir / "2026-05-07.md").exists()
        # 主便签不动
        assert (memory_dir / "memory.md").exists()

    def test_group_files_moved_to_groups(self, memory_dir: Path) -> None:
        (memory_dir / "group_123_users.md").write_text("# 群用户", encoding="utf-8")
        moved = notes.migrate_memory_layout()
        assert len(moved) == 1
        assert (memory_dir / "groups" / "group_123_users.md").exists()

    def test_idempotent(self, memory_dir: Path) -> None:
        (memory_dir / "2026-05-07.md").write_text("# 事件", encoding="utf-8")
        notes.migrate_memory_layout()
        assert notes.migrate_memory_layout() == []

    def test_existing_target_removes_duplicate(self, memory_dir: Path) -> None:
        (memory_dir / "2026-05-07.md").write_text("# 旧", encoding="utf-8")
        events = memory_dir / "events"
        events.mkdir()
        (events / "2026-05-07.md").write_text("# 新", encoding="utf-8")
        notes.migrate_memory_layout()
        assert not (memory_dir / "2026-05-07.md").exists()
        assert (events / "2026-05-07.md").read_text(encoding="utf-8") == "# 新"


class TestSmartTruncateNotes:
    def test_short_content_untouched(self) -> None:
        content = "# 指南\n一些内容"
        assert notes._smart_truncate_notes(content, 6000) == content

    def test_high_priority_sections_kept(self) -> None:
        guide = "# 记忆系统指南\n" + "指南内容\n"
        teachings = "## 主人教导（已确认）\n" + "重要准则\n"
        filler = "## 兴趣爱好\n" + ("很长的内容" * 2000 + "\n")
        content = guide + teachings + filler
        result = notes._smart_truncate_notes(content, 1000)
        assert "主人教导" in result
        assert "重要准则" in result
        assert "已折叠章节" in result
        assert "兴趣爱好" in result  # 折叠标题中列出

    def test_fold_notice_appended(self) -> None:
        content = "# 头\n" + "".join(
            f"## 章节{i}\n{'x' * 500}\n" for i in range(10)
        )
        result = notes._smart_truncate_notes(content, 1200)
        assert "已折叠章节" in result
        assert "read_section" in result
        assert len(result) < len(content)


# ==================================================================
# events 日期便签生命周期（过期检测与删除）
# ==================================================================

_TODAY = date(2026, 7, 20)


class TestListExpiredEvents:
    def test_empty_when_no_events_dir(self, tmp_path, monkeypatch) -> None:
        ws = tmp_path / "config"
        (ws / "memory").mkdir(parents=True)
        monkeypatch.setattr(notes, "_workspace_dir", ws)
        assert notes.list_expired_events(30, today=_TODAY) == []

    def test_boundary_exactly_retention_days_kept(self, events_dir: Path) -> None:
        events = events_dir / "events"
        (events / "2026-06-20.md").write_text("# 恰好 30 天", encoding="utf-8")
        (events / "2026-06-19.md").write_text("# 31 天", encoding="utf-8")

        expired = notes.list_expired_events(30, today=_TODAY)
        dates = [e.date for e in expired]
        assert dates == ["2026-06-19"]

    def test_sorted_by_date_ascending(self, events_dir: Path) -> None:
        events = events_dir / "events"
        (events / "2026-05-10.md").write_text("# a", encoding="utf-8")
        (events / "2026-05-07.md").write_text("# b", encoding="utf-8")
        (events / "2026-07-19.md").write_text("# 未过期", encoding="utf-8")

        expired = notes.list_expired_events(30, today=_TODAY)
        assert [e.date for e in expired] == ["2026-05-07", "2026-05-10"]
        assert expired[0].path == "memory/events/2026-05-07.md"
        assert expired[0].abs_path == events / "2026-05-07.md"

    def test_non_date_files_ignored(self, events_dir: Path) -> None:
        events = events_dir / "events"
        (events / "archive.md").write_text("# 非日期", encoding="utf-8")
        (events / "2026-13-01.md").write_text("# 非法日期", encoding="utf-8")
        (events / "2026-05-07.md").write_text("# 正常", encoding="utf-8")

        expired = notes.list_expired_events(30, today=_TODAY)
        assert [e.date for e in expired] == ["2026-05-07"]


class TestDeleteExpiredEvent:
    def test_delete_via_existing_entry(self, events_dir: Path) -> None:
        events = events_dir / "events"
        target = events / "2026-05-07.md"
        target.write_text("# 旧事件", encoding="utf-8")

        expired = notes.list_expired_events(30, today=_TODAY)
        assert len(expired) == 1
        assert notes.delete_memory_file(expired[0].path) is True
        assert not target.exists()
        assert notes.list_expired_events(30, today=_TODAY) == []
