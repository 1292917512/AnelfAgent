"""工具结果超限落盘测试（对齐 Claude Code persisted-output）。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from agent.mind.tools import result_pipeline
from agent.mind.tools.result_pipeline import ToolResultPipeline, _persist_oversized_result


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    from core.config import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: str(tmp_path) if k == "workspace_root" else d))
    yield tmp_path


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
