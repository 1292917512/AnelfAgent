"""思维循环结束拦截（think_loop）单元测试。

覆盖场景：
- AI 调用 end_reply 结束本轮时，若同轮存在失败工具，
  系统应生成失败反馈拦截结束，给 AI 修正参数后重试的机会；
- AI 在文字中声明了立即执行的动作却直接 end_reply（说做不一致），
  系统应拦截结束并要求其兑现承诺或明确放弃。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent.llm.types import ToolCall
from agent.mind.tools.think_loop import (
    _collect_premature_end,
    _collect_round_failures,
    _extract_error_text,
)


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


def _result(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text)


class TestCollectPrematureEnd:
    """提前结束拦截：end_reply 是本轮唯一工具调用且附带大段不可见文本。"""

    def test_only_end_reply_with_long_text_intercepts(self) -> None:
        """唯一动作是结束 + 大段文字（计划/承诺）→ 生成拦截反馈。"""
        result = _result(
            "找到 generate_image 工具了！现在先调 generate_image 画图，"
            "拿到生成路径后再用 send_photo 发给主人"
        )
        feedback = _collect_premature_end(result, [_tc("c1", "end_reply")])
        assert "系统拦截" in feedback
        assert "下一轮" in feedback  # 纠正"end_reply 后还有下一轮"的错误认知

    def test_work_tool_plus_end_reply_passes(self) -> None:
        """本轮有实际工具调用（send_message + end_reply）→ 不拦截。"""
        result = _result("好的主人，我已经把图片画好了，现在发给你看～" * 2)
        feedback = _collect_premature_end(
            result, [_tc("c1", "send_message"), _tc("c2", "end_reply")],
        )
        assert feedback == ""

    def test_short_text_passes(self) -> None:
        """简短收尾文字（< 阈值）→ 不拦截。"""
        result = _result("好的，结束啦")
        assert _collect_premature_end(result, [_tc("c1", "end_reply")]) == ""

    def test_empty_text_passes(self) -> None:
        assert _collect_premature_end(_result(""), [_tc("c1", "end_reply")]) == ""

    def test_think_blocks_stripped_before_measure(self) -> None:
        """<think> 推理块剥离后再度量：可见文本不足阈值则不拦截。"""
        result = _result(f"<think>{'很长的推理过程' * 20}</think>好的")
        assert _collect_premature_end(result, [_tc("c1", "end_reply")]) == ""
