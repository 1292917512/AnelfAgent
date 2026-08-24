"""思维循环结束拦截（think_loop）单元测试。

覆盖场景：
- AI 调用 end_reply 结束本轮时，若同轮存在失败工具，
  系统应生成失败反馈拦截结束，给 AI 修正参数后重试的机会。
"""

from __future__ import annotations

import json

from agent.llm.types import ToolCall
from agent.mind.tools.result_parse import extract_error_text
from agent.mind.tools.think_loop import (
    _collect_round_failures,
)


def _tc(tc_id: str, name: str) -> ToolCall:
    return ToolCall(id=tc_id, name=name, arguments="{}")


def _tool_msg(tc_id: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": tc_id, "content": json.dumps(payload, ensure_ascii=False)}


class TestExtractErrorText:
    def test_success_false_with_error(self) -> None:
        assert extract_error_text({"success": False, "error": "boom"}) == "boom"

    def test_ok_false_without_error(self) -> None:
        assert extract_error_text({"ok": False}) == "工具返回失败但未提供错误详情"

    def test_ok_false_falls_back_to_notes(self) -> None:
        """shell 无匹配类否定结果：notes 里的语义解释优先于兜底文案。"""
        payload = {
            "ok": False, "stdout": "", "stderr": "",
            "notes": ["注意: 命令以非零码结束但无错误输出——若是 grep/搜索类命令，"
                      "这通常表示无匹配或条件不成立"],
        }
        assert extract_error_text(payload).startswith("注意: 命令以非零码结束")

    def test_ok_false_falls_back_to_returncode(self) -> None:
        payload = {"ok": False, "stdout": "", "stderr": "", "returncode": 1}
        assert extract_error_text(payload) == "命令退出码 1（无错误输出）"

    def test_notes_take_priority_over_returncode(self) -> None:
        payload = {"ok": False, "returncode": 1, "notes": ["解释"]}
        assert extract_error_text(payload) == "解释"

    def test_long_notes_truncated(self) -> None:
        payload = {"ok": False, "notes": ["长" * 200]}
        assert extract_error_text(payload) == "长" * 150 + "…"

    def test_ok_false_falls_back_to_stderr(self) -> None:
        """shell 类工具失败 payload 无 error 键，真实原因在 stderr。"""
        payload = {"ok": False, "stdout": "", "stderr": "Connection closed by 127.0.0.1 port 7897"}
        assert extract_error_text(payload) == "Connection closed by 127.0.0.1 port 7897"

    def test_ok_false_falls_back_to_message(self) -> None:
        payload = {"ok": False, "message": "已存在相似记忆，跳过"}
        assert extract_error_text(payload) == "已存在相似记忆，跳过"

    def test_error_key_takes_priority_over_fallback(self) -> None:
        payload = {"success": False, "error": "主信号", "stderr": "噪音"}
        assert extract_error_text(payload) == "主信号"

    def test_error_key_present(self) -> None:
        assert extract_error_text({"error": "bad args"}) == "bad args"

    def test_success_result_returns_empty(self) -> None:
        assert extract_error_text({"success": True, "ok": True}) == ""

    def test_json_string_payload(self) -> None:
        assert extract_error_text('{"success": false, "error": "x"}') == "x"

    def test_non_json_string_returns_empty(self) -> None:
        assert extract_error_text("plain text result") == ""

    def test_non_dict_returns_empty(self) -> None:
        assert extract_error_text([1, 2, 3]) == ""


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
