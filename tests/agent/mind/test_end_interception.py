"""思维循环结束拦截（think_loop._collect_round_failures）单元测试。

覆盖场景：AI 调用 end_reply 结束本轮时，若同轮存在失败工具，
系统应生成失败反馈拦截结束，给 AI 修正参数后重试的机会。
"""

from __future__ import annotations

import json

from agent.llm.types import ToolCall
from agent.mind.tools.think_loop import _collect_round_failures, _extract_error_text


def _tc(tc_id: str, name: str) -> ToolCall:
    return ToolCall(id=tc_id, name=name, arguments="{}")


def _tool_msg(tc_id: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": tc_id, "content": json.dumps(payload, ensure_ascii=False)}


class TestExtractErrorText:
    def test_success_false_with_error(self) -> None:
        assert _extract_error_text({"success": False, "error": "boom"}) == "boom"

    def test_ok_false_without_error(self) -> None:
        assert _extract_error_text({"ok": False}) == "未知错误"

    def test_error_key_present(self) -> None:
        assert _extract_error_text({"error": "bad args"}) == "bad args"

    def test_success_result_returns_empty(self) -> None:
        assert _extract_error_text({"success": True, "ok": True}) == ""

    def test_json_string_payload(self) -> None:
        assert _extract_error_text('{"success": false, "error": "x"}') == "x"

    def test_non_json_string_returns_empty(self) -> None:
        assert _extract_error_text("plain text result") == ""

    def test_non_dict_returns_empty(self) -> None:
        assert _extract_error_text([1, 2, 3]) == ""


class TestCollectRoundFailures:
    def test_no_failures_returns_empty(self) -> None:
        tool_calls = [_tc("c1", "send_message"), _tc("c2", "end_reply")]
        tool_chain = [
            _tool_msg("c1", {"success": True, "target_id": "123"}),
            _tool_msg("c2", {"ok": True, "action": "end_reply"}),
        ]
        assert _collect_round_failures(tool_chain, tool_calls) == ""

    def test_plain_tool_failure(self) -> None:
        tool_calls = [_tc("c1", "send_message"), _tc("c2", "end_reply")]
        tool_chain = [
            _tool_msg("c1", {"success": False, "error": "发送消息失败: 'int' object has no attribute 'strip'"}),
            _tool_msg("c2", {"ok": True, "action": "end_reply"}),
        ]
        feedback = _collect_round_failures(tool_chain, tool_calls)
        assert "send_message" in feedback
        assert "'int' object has no attribute 'strip'" in feedback
        assert "系统拦截" in feedback

    def test_ignores_previous_round_results(self) -> None:
        """只统计本轮 tool_calls 对应的结果，历史轮次失败不影响。"""
        tool_calls = [_tc("c2", "end_reply")]
        tool_chain = [
            _tool_msg("c1", {"success": False, "error": "上一轮的错误"}),
            _tool_msg("c2", {"ok": True, "action": "end_reply"}),
        ]
        assert _collect_round_failures(tool_chain, tool_calls) == ""

    def test_empty_tool_calls_returns_empty(self) -> None:
        assert _collect_round_failures([], []) == ""
