"""notes_guard 便签键空间守卫单元测试（分类矩阵 / 拦截与归因文案）。"""

from __future__ import annotations

import json

import pytest

from core import path as core_path
from entities.filesystem import notes_guard


@pytest.fixture()
def layout(tmp_path, monkeypatch):
    """隔离布局：workspace 为 shell cwd，dataroot/memory 为便签树。"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    md = tmp_path / "dataroot" / "memory"
    md.mkdir(parents=True)
    monkeypatch.setitem(core_path._PATH_OVERRIDES, "MEMORY_DIR", str(md))
    return ws, md


class TestFindShellViolation:
    def test_key_misuse_when_only_in_notes_root(self, layout):
        ws, md = layout
        (md / "hb.md").write_text("x", encoding="utf-8")
        violation = notes_guard.find_shell_violation("tail -n 50 memory/hb.md", str(ws))
        assert violation == notes_guard.ShellViolation("memory/hb.md", "key_misuse")

    def test_workspace_file_wins_over_key_form(self, layout):
        ws, md = layout
        (md / "hb.md").write_text("x", encoding="utf-8")
        (ws / "memory").mkdir()
        (ws / "memory" / "hb.md").write_text("x", encoding="utf-8")
        assert notes_guard.find_shell_violation("cat memory/hb.md", str(ws)) is None

    def test_key_in_neither_is_allowed(self, layout):
        ws, _ = layout
        assert notes_guard.find_shell_violation("cat memory/gone.md", str(ws)) is None

    def test_absolute_path_into_notes_root(self, layout):
        ws, md = layout
        target = md / "hb.md"
        target.write_text("x", encoding="utf-8")
        violation = notes_guard.find_shell_violation(f"cat {target}", str(ws))
        assert violation is not None and violation.kind == "notes_path"

    def test_relative_path_resolving_into_notes_root(self, layout):
        ws, md = layout
        (md / "hb.md").write_text("x", encoding="utf-8")
        violation = notes_guard.find_shell_violation("cat ../dataroot/memory/hb.md", str(ws))
        assert violation is not None and violation.kind == "notes_path"

    def test_notes_root_dir_itself(self, layout):
        ws, md = layout
        violation = notes_guard.find_shell_violation(f"ls {md}", str(ws))
        assert violation is not None and violation.kind == "notes_path"

    def test_sibling_memory2_not_matched(self, layout):
        ws, md = layout
        sibling = md.parent / "memory2"
        sibling.mkdir()
        (sibling / "hb.md").write_text("x", encoding="utf-8")
        assert notes_guard.find_shell_violation(f"cat {sibling / 'hb.md'}", str(ws)) is None

    def test_nonexistent_path_in_notes_root_blocked(self, layout):
        ws, md = layout
        # 写入也不要求文件已存在——绕过 notes 工具的新建同样拦截
        violation = notes_guard.find_shell_violation(f"echo hi > {md / 'new.md'}", str(ws))
        assert violation is not None and violation.kind == "notes_path"

    def test_plain_command_untouched(self, layout):
        ws, _ = layout
        assert notes_guard.find_shell_violation("echo hello", str(ws)) is None


class TestViolationError:
    def test_key_misuse_payload(self):
        result = json.loads(notes_guard.shell_violation_error(
            notes_guard.ShellViolation("memory/hb.md", "key_misuse")))
        assert result["guard"] == "memory_notes"
        assert result["cause"] == "param"
        assert result["retryable"] is False
        assert result["token"] == "memory/hb.md"
        assert "便签索引键" in result["error"]
        assert "read_memory_file" in result["hint"]

    def test_notes_path_payload(self):
        result = json.loads(notes_guard.shell_violation_error(
            notes_guard.ShellViolation("/data/memory/hb.md", "notes_path")))
        assert "便签树" in result["error"]


class TestKeyTokenAndNote:
    def test_find_key_token_first_match(self):
        assert notes_guard.find_key_token("cat a.txt memory/b.md memory/c.md") == "memory/b.md"

    def test_find_key_token_none(self):
        assert notes_guard.find_key_token("cat a.txt") is None

    def test_key_space_note_states_namespace(self):
        note = notes_guard.key_space_note("memory/gone.md")
        assert "memory/gone.md" in note and "便签索引键" in note


class TestFilesystemGuards:
    def test_hint_absolute_md_in_notes_root(self, layout):
        _, md = layout
        target = md / "hb.md"
        target.write_text("x", encoding="utf-8")
        hint = notes_guard.memory_note_hint(str(target))
        assert hint is not None and "memory/hb.md" in hint and "notes 组工具" in hint

    def test_hint_key_form_existing(self, layout):
        _, md = layout
        (md / "hb.md").write_text("x", encoding="utf-8")
        hint = notes_guard.memory_note_hint("memory/hb.md")
        assert hint is not None and "notes 组工具" in hint

    def test_hint_miss_returns_none(self, layout):
        assert notes_guard.memory_note_hint("memory/gone.md") is None
        assert notes_guard.memory_note_hint("src/main.py") is None

    def test_create_guard_blocks_key_form(self):
        result = json.loads(notes_guard.key_create_guard("memory/new.md"))
        assert result["cause"] == "param"
        assert notes_guard.key_create_guard("notes/new.md") is None
