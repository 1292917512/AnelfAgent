"""工具结果预算截断（agent.mind.result_budget + think_loop 集成）单元测试。"""

from __future__ import annotations

from agent.mind.result_budget import (
    PINNED_TOOLS,
    ResultBudget,
    budget_for_context_window,
    resolve_result_limit,
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
