"""edit_file / read_file / write_file 工具集成测试。

移植自 Claude Code FileEditTool/FileReadTool/FileWriteTool 的校验语义，
见 docs/refactor/01-claudecode-tools.md。
"""

from __future__ import annotations

import json
import os
import time

import pytest

from entities.filesystem import file_state, tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """把沙箱 workspace 指向临时目录，并隔离文件状态缓存。"""
    monkeypatch.setattr(tools, "_load_config", lambda: None)
    monkeypatch.setattr(tools, "_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(tools, "_SANDBOX", True)
    file_state.clear_scope("_global")
    yield tmp_path
    file_state.clear_scope("_global")


def _read(fp, **kwargs):
    return tools.read_file(str(fp), **kwargs)


def _edit(fp, old, new, replace_all=False):
    return json.loads(tools.edit_file(str(fp), old, new, replace_all))


# ------------------------------------------------------------------
# edit_file
# ------------------------------------------------------------------

class TestEditFile:
    def test_same_strings_rejected(self, workspace):
        result = _edit(workspace / "a.txt", "x", "x")
        assert result["code"] == 1

    def test_create_file_with_empty_old(self, workspace):
        result = _edit(workspace / "sub/a.txt", "", "hello")
        assert result["ok"]
        assert (workspace / "sub/a.txt").read_text() == "hello"

    def test_missing_file_suggests_similar(self, workspace):
        (workspace / "readme.txt").write_text("x")
        result = _edit(workspace / "readm.txt", "x", "y")
        assert result["code"] == 4
        assert "readme.txt" in result["error"]

    def test_empty_old_on_existing_rejected(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("content")
        result = _edit(fp, "", "x")
        assert result["code"] == 3

    def test_edit_without_read_rejected(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("hello world")
        result = _edit(fp, "world", "there")
        assert result["code"] == 6
        assert "尚未读取" in result["error"]

    def test_edit_after_read_succeeds(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("hello world")
        _read(fp)
        result = _edit(fp, "world", "there")
        assert result["ok"]
        assert fp.read_text() == "hello there"

    def test_edit_after_partial_read_rejected(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("l1\nl2\nl3\nl4\nl5")
        _read(fp, offset=1, limit=2)
        result = _edit(fp, "l3", "x")
        assert result["code"] == 6
        assert "部分读取" in result["error"]

    def test_uniqueness_failure_teaches(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("foo bar foo")
        _read(fp)
        result = _edit(fp, "foo", "baz")
        assert result["code"] == 9
        assert "2 处匹配" in result["error"]
        assert "replace_all" in result["error"]

    def test_replace_all(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("foo bar foo")
        _read(fp)
        result = _edit(fp, "foo", "baz", replace_all=True)
        assert result["ok"]
        assert result["replaced"] == 2
        assert fp.read_text() == "baz bar baz"

    def test_replace_all_string_bool_tolerated(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("foo foo")
        _read(fp)
        result = _edit(fp, "foo", "baz", replace_all="true")
        assert result["ok"] and result["replaced"] == 2

    def test_string_not_found(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("hello")
        _read(fp)
        result = _edit(fp, "missing", "x")
        assert result["code"] == 8

    def test_curly_quotes_matched_and_preserved(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("say “hello” loudly")
        _read(fp)
        result = _edit(fp, '"hello"', '"hi"')
        assert result["ok"]
        assert fp.read_text() == "say “hi” loudly"

    def test_crlf_roundtrip(self, workspace):
        fp = workspace / "a.txt"
        fp.write_bytes(b"line1\r\nline2\r\n")
        _read(fp)
        result = _edit(fp, "line2", "changed")
        assert result["ok"]
        assert fp.read_bytes() == b"line1\r\nchanged\r\n"

    def test_stale_file_rejected(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("original")
        _read(fp)
        fp.write_text("externally modified")
        os.utime(fp, (time.time() + 2, time.time() + 2))
        result = _edit(fp, "externally", "x")
        assert result["code"] == 6
        assert "已被修改" in result["error"]

    def test_delete_line_cleanly(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("x\ny\nz")
        _read(fp)
        result = _edit(fp, "y", "")
        assert result["ok"]
        assert fp.read_text() == "x\nz"

    def test_trailing_whitespace_stripped(self, workspace):
        fp = workspace / "a.py"
        fp.write_text("a = 1")
        _read(fp)
        result = _edit(fp, "a = 1", "a = 2   \n")
        assert result["ok"]
        assert fp.read_text() == "a = 2\n"

    def test_markdown_trailing_spaces_preserved(self, workspace):
        fp = workspace / "a.md"
        fp.write_text("line")
        _read(fp)
        result = _edit(fp, "line", "line  \nnext")
        assert result["ok"]
        assert fp.read_text() == "line  \nnext"

    def test_consecutive_edits_without_reread(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("a b c")
        _read(fp)
        assert _edit(fp, "a", "x")["ok"]
        assert _edit(fp, "c", "z")["ok"]
        assert fp.read_text() == "x b z"


# ------------------------------------------------------------------
# read_file
# ------------------------------------------------------------------

class TestReadFile:
    def test_line_numbers_present(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("alpha\nbeta")
        out = _read(fp)
        assert "1→alpha" in out and "2→beta" in out

    def test_offset_limit(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("\n".join(f"line{i}" for i in range(1, 11)))
        out = _read(fp, offset=3, limit=2)
        assert "3→line3" in out and "4→line4" in out
        assert "5→line5" not in out
        assert "offset=5" in out  # 继续读取指引

    def test_dedup_stub_on_reread(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("hello")
        _read(fp)
        result = json.loads(_read(fp))
        assert result["unchanged"] is True

    def test_reread_after_change_returns_content(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("hello")
        _read(fp)
        time.sleep(0.01)
        fp.write_text("changed")
        os.utime(fp, None)
        out = _read(fp)
        assert "changed" in out

    def test_empty_file_note(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("")
        out = _read(fp)
        assert "内容为空" in out

    def test_oversize_file_requires_pagination(self, workspace):
        fp = workspace / "big.txt"
        fp.write_text("x" * (300 * 1024))
        result = json.loads(_read(fp))
        assert "offset/limit" in result["error"]

    def test_binary_returns_metadata(self, workspace):
        fp = workspace / "a.png"
        fp.write_bytes(b"\x89PNG")
        result = json.loads(_read(fp))
        assert result["type"] == "binary"


# ------------------------------------------------------------------
# write_file
# ------------------------------------------------------------------

class TestWriteFile:
    def test_new_file_writes_directly(self, workspace):
        result = json.loads(tools.write_file(str(workspace / "new.txt"), "data"))
        assert result["ok"]

    def test_existing_file_requires_read(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("old")
        result = json.loads(tools.write_file(str(fp), "new"))
        assert "尚未读取" in result["error"]

    def test_write_after_read_then_edit(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("old")
        _read(fp)
        assert json.loads(tools.write_file(str(fp), "new content"))["ok"]
        # write 更新了缓存，可直接 edit 无需重读
        assert _edit(fp, "new", "newer")["ok"]
        assert fp.read_text() == "newer content"


# ------------------------------------------------------------------
# append_file
# ------------------------------------------------------------------

class TestAppendFile:
    def test_append_refreshes_state(self, workspace):
        fp = workspace / "a.txt"
        fp.write_text("hello")
        _read(fp)
        assert json.loads(tools.append_file(str(fp), " world"))["ok"]
        # 追加后缓存已刷新，edit 不应报过期
        assert _edit(fp, "world", "there")["ok"]
