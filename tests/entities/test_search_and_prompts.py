"""search_files 增强与工具长 prompt 测试。"""

from __future__ import annotations

import json
import os
import time

import pytest

from entities.filesystem import tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_load_config", lambda: None)
    monkeypatch.setattr(tools, "_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(tools, "_SANDBOX", True)
    yield tmp_path


class TestSearchFiles:
    def test_glob_mode_sorted_by_mtime(self, workspace):
        old = workspace / "old.txt"
        new = workspace / "new.txt"
        old.write_text("x")
        time.sleep(0.01)
        new.write_text("x")
        result = json.loads(tools.search_files(str(workspace), "*.txt"))
        assert result["results"][0]["name"] == "new.txt"

    def test_content_pattern_finds_hits(self, workspace):
        (workspace / "a.py").write_text("def foo():\n    pass\n# TODO: fix\n")
        (workspace / "b.py").write_text("def bar():\n    pass\n")
        result = json.loads(tools.search_files(str(workspace), "**/*.py", content_pattern="TODO"))
        assert result["count"] == 1
        assert result["results"][0]["matches"] == ["3:# TODO: fix"]

    def test_content_pattern_invalid_regex(self, workspace):
        result = json.loads(tools.search_files(str(workspace), "*", content_pattern="(["))
        assert "正则" in result["error"]

    def test_content_pattern_no_duplicates_dirs(self, workspace):
        (workspace / "sub").mkdir()
        (workspace / "sub/x.txt").write_text("needle\n")
        result = json.loads(tools.search_files(str(workspace), "**/*", content_pattern="needle"))
        assert result["count"] == 1
        assert result["results"][0]["path"].endswith("x.txt")


class TestLongPrompts:
    def test_schema_description_contains_usage_rules(self):
        from core.entity import EntityRegistry
        import entities.filesystem.tools  # noqa: F401

        schemas = {s["function"]["name"]: s["function"]["description"]
                   for s in EntityRegistry.get_tool_schemas()}
        assert "行号前缀" in schemas["read_file"]
        assert "replace_all" in schemas["edit_file"]
        assert "search_files" in schemas["run_shell_command"]  # 工具偏好表
        assert "edit_file" in schemas["write_file"]  # 优先用 edit 的引导
