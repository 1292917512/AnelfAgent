"""工具结果管线测试：空结果占位 + 超限落盘 persisted-output（对齐 Claude Code）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.mind.tools.result_pipeline import ToolResultPipeline, _persist_oversized_result


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    from core.config import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: str(tmp_path) if k == "workspace_root" else d))
    yield tmp_path


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


class TestPersistOversized:
    def test_small_output_not_persisted(self, workspace):
        assert _persist_oversized_result("x", "y" * 1000) is None

    def test_large_output_persisted_with_preview(self, workspace):
        output = "A" * 60_000
        result = _persist_oversized_result("web_fetch", output)
        assert result is not None
        assert "<persisted-output>" in result
        assert "60" in result  # 字符数说明
        # 落盘文件存在且内容完整
        out_dir = workspace / ".tool-results"
        files = list(out_dir.glob("*.txt"))
        assert len(files) == 1
        assert files[0].read_text() == output

    def test_pipeline_end_to_end(self, workspace):
        pipeline = ToolResultPipeline(SimpleNamespace(), None)
        pipeline.begin_turn()
        result = pipeline.process("web_fetch", "{}", "B" * 60_000)
        assert "<persisted-output>" in result
        # 持久化后的预览文本远小于原文
        assert len(result) < 10_000

    def test_persist_failure_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            "entities.filesystem.shell_state.persist_output",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        assert _persist_oversized_result("x", "y" * 60_000) is None


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
