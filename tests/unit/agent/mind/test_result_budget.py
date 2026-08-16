"""工具结果体积控制单元测试：预算截断（result_budget）+ 管线落盘（result_pipeline）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.mind.result_budget import (
    ResultBudget,
    budget_for_context_window,
    resolve_result_limit,
)
from agent.mind.tools.result_pipeline import (
    ToolResultPipeline,
    _persist_oversized_result,
)
from agent.mind.tools.think_loop import _truncate_tool_output


class TestBudgetComputation:
    def test_fallback_for_unknown_window(self) -> None:
        budget = budget_for_context_window(0)
        assert budget.per_result_chars == 8_000
        assert budget.per_turn_chars == 24_000

    def test_small_window_clamped_to_floor(self) -> None:
        budget = budget_for_context_window(8_000)  # 8K tokens → 32K chars
        assert budget.per_result_chars == 8_000   # 15% = 4800 → 地板 8000
        assert budget.per_turn_chars == 16_000    # 30% = 9600 → 地板 16000

    def test_large_window_clamped_to_cap(self) -> None:
        budget = budget_for_context_window(1_000_000)  # 1M tokens
        assert budget.per_result_chars == 100_000
        assert budget.per_turn_chars == 200_000

    def test_typical_window(self) -> None:
        budget = budget_for_context_window(128_000)  # 128K tokens → 512K chars
        assert budget.per_result_chars == int(512_000 * 0.15)  # 76800
        assert budget.per_turn_chars == int(512_000 * 0.30)    # 153600


class TestResolveLimit:
    def test_pinned_tool_unlimited(self) -> None:
        budget = budget_for_context_window(128_000)
        assert resolve_result_limit("send_message", budget) == 0
        assert resolve_result_limit("end_reply", budget) == 0

    def test_normal_tool(self) -> None:
        budget = budget_for_context_window(128_000)
        assert resolve_result_limit("web_search", budget) == budget.per_result_chars


class TestTruncateWithBudget:
    def test_pinned_tool_not_truncated(self) -> None:
        budget = ResultBudget(per_result_chars=100, per_turn_chars=200)
        output = "x" * 10_000
        assert _truncate_tool_output("send_message", output, budget=budget) == output

    def test_dynamic_limit_applied(self) -> None:
        budget = ResultBudget(per_result_chars=1000, per_turn_chars=5000)
        output = "y" * 5_000
        result = _truncate_tool_output("web_search", output, budget=budget)
        assert len(result) < len(output)
        assert "已自动截断" in result

    def test_html_stricter_limit(self) -> None:
        budget = ResultBudget(per_result_chars=100_000, per_turn_chars=200_000)
        output = "<!DOCTYPE html><html><body>" + "z" * 10_000 + "</body></html>"
        result = _truncate_tool_output("fetch_page", output, budget=budget)
        assert len(result) <= 3000 + 200  # HTML 特例 3000 + 截断标记

    def test_no_budget_fallback(self) -> None:
        output = "w" * 10_000
        result = _truncate_tool_output("web_search", output)
        assert len(result) < len(output)

    def test_short_output_untouched(self) -> None:
        budget = ResultBudget(per_result_chars=1000, per_turn_chars=5000)
        output = "short"
        assert _truncate_tool_output("web_search", output, budget=budget) == output

    def test_json_structure_preserved(self) -> None:
        import json
        budget = ResultBudget(per_result_chars=2000, per_turn_chars=5000)
        output = json.dumps({"success": True, "data": ["item" * 100] * 50})
        result = _truncate_tool_output("web_search", output, budget=budget)
        parsed = json.loads(result)  # JSON 结构化裁剪保持可解析
        assert parsed is not None


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    from core.config import ConfigManager
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: str(tmp_path) if k == "workspace_root" else d))
    yield tmp_path


def _pipeline():
    from agent.mind.tools.result_pipeline import ToolResultPipeline

    mind = SimpleNamespace(get_model_context_length=lambda: 0)
    return ToolResultPipeline(mind)


class TestEmptyResultPlaceholder:
    """空结果占位（对齐 Claude Code）：空白输出给模型明确的完成语义。"""

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
