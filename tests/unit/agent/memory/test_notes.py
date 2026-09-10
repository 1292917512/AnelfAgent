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


# ==================================================================
# 自动记忆状态区块（心跳受管区）
# ==================================================================

class TestUpdateMemoryStatusBlock:
    def _write_main(self, memory_dir: Path, content: str) -> Path:
        main = memory_dir / "memory.md"
        main.write_text(content, encoding="utf-8")
        return main

    def test_insert_after_dynamic_marker(self, memory_dir: Path) -> None:
        main = self._write_main(memory_dir, "# 指南\n\n# 当前状态\n\n## 手写区\n内容\n")
        assert notes.update_memory_status_block("## 记忆系统状态\n- 活跃记忆：10 条") is True
        text = main.read_text(encoding="utf-8")
        assert text.index("# 当前状态") < text.index(notes.AUTO_STATUS_BEGIN)
        assert "活跃记忆：10 条" in text
        assert "## 手写区" in text  # 手写内容保留

    def test_replace_existing_block_keeps_handwritten(self, memory_dir: Path) -> None:
        main = self._write_main(
            memory_dir,
            "# 指南\n\n# 当前状态\n\n"
            f"{notes.AUTO_STATUS_BEGIN}\n旧状态\n{notes.AUTO_STATUS_END}\n\n## 手写区\n内容\n",
        )
        notes.update_memory_status_block("新状态")
        text = main.read_text(encoding="utf-8")
        assert "新状态" in text and "旧状态" not in text
        assert "## 手写区" in text

    def test_no_change_no_write(self, memory_dir: Path) -> None:
        main = self._write_main(memory_dir, "# 指南\n\n# 当前状态\n")
        assert notes.update_memory_status_block("状态A") is True
        mtime = main.stat().st_mtime_ns
        assert notes.update_memory_status_block("状态A") is False
        assert main.stat().st_mtime_ns == mtime


# ==================================================================
# 「当前状态」分界容错（标题层级/编号漂移）与受管区块写保护
# ==================================================================

class TestStatusHeadingSplit:
    def _write_main(self, memory_dir: Path, content: str) -> Path:
        main = memory_dir / "memory.md"
        main.write_text(content, encoding="utf-8")
        return main

    def test_numbered_subheading_splits(self, memory_dir: Path) -> None:
        """「## 四、当前状态」式编号子标题同样作为静态/动态分界。"""
        self._write_main(
            memory_dir,
            "# 主便签\n\n## 一、铁律速查\n铁律内容\n\n"
            "## 四、当前状态\n\n## 记忆系统状态\n状态内容\n",
        )
        static = notes.build_static_guide()
        dynamic = notes.build_dynamic_notes()
        assert "铁律内容" in static
        assert "状态内容" not in static  # 状态区不进 stable 层
        assert "当前状态" in dynamic

    def test_auto_block_not_double_injected_into_static(self, memory_dir: Path) -> None:
        """分界命中后，AUTO 状态区块不进静态指南（仅经 build_memory_status_block 注入）。"""
        self._write_main(
            memory_dir,
            "# 指南\n\n## 四、当前状态\n\n"
            f"{notes.AUTO_STATUS_BEGIN}\n心跳状态正文\n{notes.AUTO_STATUS_END}\n",
        )
        static = notes.build_static_guide()
        assert "心跳状态正文" not in static
        status = notes.build_memory_status_block()
        assert "心跳状态正文" in status

    def test_insert_after_numbered_heading(self, memory_dir: Path) -> None:
        main = self._write_main(memory_dir, "# 指南\n\n## 四、当前状态\n\n## 手写区\n内容\n")
        assert notes.update_memory_status_block("状态A") is True
        text = main.read_text(encoding="utf-8")
        assert text.index("## 四、当前状态") < text.index(notes.AUTO_STATUS_BEGIN)
        assert "状态A" in text and "## 手写区" in text

    def test_no_heading_appends_canonical_marker(self, memory_dir: Path) -> None:
        main = self._write_main(memory_dir, "# 指南\n")
        assert notes.update_memory_status_block("状态B") is True
        text = main.read_text(encoding="utf-8")
        assert "# 当前状态" in text and "状态B" in text

    def test_table_row_mention_not_treated_as_heading(self, memory_dir: Path) -> None:
        """表格行内出现「当前状态」字样不误判为分界标题。"""
        self._write_main(
            memory_dir,
            "# 指南\n\n| memory.md | 指南 + 当前状态 | 不限 |\n",
        )
        static = notes.build_static_guide()
        assert "指南 + 当前状态" in static


class TestManagedBlockProtection:
    BLOCK = f"{notes.AUTO_STATUS_BEGIN}\n系统维护内容\n{notes.AUTO_STATUS_END}"

    def _write_file(self, memory_dir: Path, rel: str = "memory/note1.md") -> Path:
        target = memory_dir.parent / rel
        target.write_text(f"# 笔记\n\n{self.BLOCK}\n\n## 手写区\n内容\n", encoding="utf-8")
        return target

    def test_write_memory_file_rejects_block_change(self, memory_dir: Path) -> None:
        self._write_file(memory_dir)
        with pytest.raises(ValueError, match="系统受管区块"):
            notes.write_memory_file(
                "memory/note1.md", "# 笔记\n\n篡改区块\n\n## 手写区\n内容\n",
            )

    def test_write_memory_file_allows_outside_edit(self, memory_dir: Path) -> None:
        target = self._write_file(memory_dir)
        notes.write_memory_file(
            "memory/note1.md", f"# 笔记\n\n{self.BLOCK}\n\n## 手写区\n新内容\n",
        )
        assert "新内容" in target.read_text(encoding="utf-8")

    def test_patch_rejects_edit_inside_block(self, memory_dir: Path) -> None:
        self._write_file(memory_dir)
        with pytest.raises(ValueError, match="系统受管区块"):
            notes.patch_memory_file_content("memory/note1.md", "系统维护内容", "篡改")

    def test_patch_outside_block_allowed(self, memory_dir: Path) -> None:
        target = self._write_file(memory_dir)
        notes.patch_memory_file_content("memory/note1.md", "手写区\n内容", "手写区\n新内容")
        assert "新内容" in target.read_text(encoding="utf-8")

    def test_edit_lines_rejects_block_overlap(self, memory_dir: Path) -> None:
        self._write_file(memory_dir)
        with pytest.raises(ValueError, match="系统受管区块"):
            notes.edit_file_lines("memory/note1.md", 3, 5, "篡改")

    def test_write_section_rejects_managed_section(self, memory_dir: Path) -> None:
        self._write_file(memory_dir)
        with pytest.raises(ValueError, match="系统受管区块"):
            notes.write_section_content("memory/note1.md", "# 笔记", "整节覆写")

    def test_delete_section_rejects_managed_section(self, memory_dir: Path) -> None:
        target = self._write_file(memory_dir)
        with pytest.raises(ValueError, match="系统受管区块"):
            notes.delete_section_content("memory/note1.md", "# 笔记")
        assert self.BLOCK in target.read_text(encoding="utf-8")

    def test_append_allowed(self, memory_dir: Path) -> None:
        target = self._write_file(memory_dir)
        notes.append_to_memory_file("memory/note1.md", "追加行\n")
        text = target.read_text(encoding="utf-8")
        assert "追加行" in text and self.BLOCK in text

    def test_save_notes_content_rejects_block_removal(self, memory_dir: Path) -> None:
        (memory_dir / "memory.md").write_text(
            f"# 主便签\n\n{self.BLOCK}\n", encoding="utf-8",
        )
        with pytest.raises(ValueError, match="系统受管区块"):
            notes.save_notes_content("# 主便签\n")


class TestStatusBlockTagOverview:
    async def test_tag_bloat_quiet_below_threshold(self, memory_dir: Path, store) -> None:
        """标签空间健康时不注入标签行（常态零占用）。"""
        from types import SimpleNamespace

        from agent.heartbeat.engine import HeartbeatEngine
        from agent.memory.memory_types import MemoryEntry, MemoryType

        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="tagfact 主人喜欢火锅",
            tags=["type:fact", "topic:火锅", "user:qq:1"], importance=0.7,
        ))
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        engine.mind = SimpleNamespace(memory_store=store)
        await engine._write_memory_status()
        text = (memory_dir / "memory.md").read_text(encoding="utf-8")
        assert "标签索引" not in text

    async def test_tag_bloat_warns_above_threshold(
        self, memory_dir: Path, store, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """标签总数超阈值时注入膨胀提醒（高频明细走 memory_index，不进 prompt）。"""
        from types import SimpleNamespace

        from agent.heartbeat.engine import HeartbeatEngine

        monkeypatch.setattr(
            "core.config.get_config_int",
            lambda key, default=0: 2 if key == "memory_tag_bloat_threshold" else default,
        )
        from agent.memory.memory_types import MemoryEntry, MemoryType

        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="tagfact 主人喜欢火锅",
            tags=["type:fact", "topic:火锅", "user:qq:1"], importance=0.7,
        ))
        engine = HeartbeatEngine.__new__(HeartbeatEngine)
        engine.mind = SimpleNamespace(
            memory_store=SimpleNamespace(
                get_type_counts=store.get_type_counts,
                count_archived=store.count_archived,
                list_tags=store.list_tags,
            )
        )
        await engine._write_memory_status()
        text = (memory_dir / "memory.md").read_text(encoding="utf-8")
        assert "存在膨胀" in text and "memory_index" in text


# ==================================================================
# 记忆体系铁律文档（config/memory_rules.md）
# ==================================================================

class TestRulesDoc:
    @pytest.fixture
    def rules_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        from agent.memory import rules_doc

        target = tmp_path / "config" / "memory_rules.md"
        monkeypatch.setattr(rules_doc, "rules_path", lambda: target)
        monkeypatch.setattr(rules_doc, "_cache", None)
        return target

    def test_seed_default_on_missing(self, rules_file: Path) -> None:
        from agent.memory import rules_doc

        text = rules_doc.load_rules()
        assert text == rules_doc.DEFAULT_RULES
        assert rules_file.read_text(encoding="utf-8") == rules_doc.DEFAULT_RULES

    def test_save_and_load_roundtrip(self, rules_file: Path) -> None:
        from agent.memory import rules_doc

        rules_doc.load_rules()  # 种子落盘
        rules_doc.save_rules("自定义铁律文档\n第二行")
        assert rules_doc.load_rules() == "自定义铁律文档\n第二行"

    def test_external_edit_picked_up(self, rules_file: Path) -> None:
        """手工编辑文件（绕过 save_rules）经 mtime 失效被读取到。"""
        from agent.memory import rules_doc

        rules_doc.load_rules()
        rules_file.write_text("手工编辑的内容", encoding="utf-8")
        assert rules_doc.load_rules() == "手工编辑的内容"
