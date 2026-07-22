"""A2/A3 工具结果管线测试：空结果占位 + 超限落盘 persisted-output。"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from agent.mind.tools.result_pipeline import ToolResultPipeline


def _pipeline() -> ToolResultPipeline:
    mind = SimpleNamespace(get_model_context_length=lambda: 0)
    return ToolResultPipeline(mind)


class TestEmptyResultPlaceholder:
    def test_empty_string(self):
        assert _pipeline().process("read_file", "{}", "") == "(read_file 执行完成，无输出)"

    def test_whitespace_only(self):
        assert _pipeline().process("search_files", "{}", "  \n ") == "(search_files 执行完成，无输出)"

    def test_normal_output_untouched(self):
        out = _pipeline().process("read_file", "{}", "hello")
        assert out == "hello"


class TestPersistedOutput:
    def test_oversized_result_persisted(self, tmp_path, monkeypatch):
        from core.config import ConfigManager
        monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: str(tmp_path) if k == "workspace_root" else d))
        big = "x" * 60_000
        out = _pipeline().process("run_shell_command", "{}", big)
        assert "<persisted-output>" in out
        assert ".tool-results" in out
        # 落盘文件内容完整
        results_dir = tmp_path / ".tool-results"
        files = list(results_dir.glob("shell-*.txt"))
        assert files and files[0].read_text() == big

    def test_small_result_not_persisted(self, tmp_path):
        out = _pipeline().process("read_file", "{}", "small")
        assert "<persisted-output>" not in out
