"""工具调用守卫（agent.mind.guardrails）单元测试。"""

from __future__ import annotations

import json

from agent.mind.guardrails import (
    GuardrailConfig,
    GuardrailController,
    ToolCallSignature,
    append_guardrail_guidance,
    classify_tool_failure,
    is_idempotent_tool,
    synthetic_block_result,
)


def _config(**overrides) -> GuardrailConfig:
    base = dict(
        enabled=True,
        hard_stop_enabled=False,
        exact_failure_warn_after=2,
        exact_failure_block_after=3,
        same_tool_failure_warn_after=2,
        same_tool_failure_halt_after=4,
        no_progress_warn_after=2,
        no_progress_block_after=3,
    )
    base.update(overrides)
    return GuardrailConfig(**base)


_ERROR_RESULT = json.dumps({"error": "boom"}, ensure_ascii=False)
_OK_RESULT = json.dumps({"ok": True}, ensure_ascii=False)


class TestSignature:
    def test_same_args_same_signature(self) -> None:
        s1 = ToolCallSignature.from_call("t", '{"a": 1, "b": 2}')
        s2 = ToolCallSignature.from_call("t", '{"b": 2, "a": 1}')
        assert s1 == s2, "参数顺序不影响签名"

    def test_different_args_different_signature(self) -> None:
        s1 = ToolCallSignature.from_call("t", '{"a": 1}')
        s2 = ToolCallSignature.from_call("t", '{"a": 2}')
        assert s1 != s2

    def test_invalid_json_fallback(self) -> None:
        s = ToolCallSignature.from_call("t", "not json")
        assert s.tool_name == "t"


class TestFailureClassification:
    def test_error_key(self) -> None:
        assert classify_tool_failure('{"error": "x"}') is True

    def test_success_false(self) -> None:
        assert classify_tool_failure('{"success": false}') is True

    def test_ok_result(self) -> None:
        assert classify_tool_failure('{"ok": true}') is False

    def test_plain_text(self) -> None:
        assert classify_tool_failure("普通文本结果") is False

    def test_error_prefix_text(self) -> None:
        assert classify_tool_failure("error: something broke") is True

    def test_null_error_field_is_success(self) -> None:
        """成功结果携带 {"error": null/""} 字段不得误判为失败。"""
        assert classify_tool_failure('{"error": null, "data": 1}') is False
        assert classify_tool_failure('{"error": "", "data": 1}') is False
        assert classify_tool_failure('{"error": "真实错误"}') is True


class TestGuardrailController:
    def test_exact_failure_warn(self) -> None:
        ctl = GuardrailController(_config())
        args = '{"q": "x"}'
        d1 = ctl.after_call("web_search", args, _ERROR_RESULT)
        assert d1.action == "allow"
        d2 = ctl.after_call("web_search", args, _ERROR_RESULT)
        assert d2.action == "warn"
        assert d2.reason == "repeated_exact_failure_warning"
        assert d2.count == 2

    def test_same_tool_failure_warn_and_halt(self) -> None:
        ctl = GuardrailController(_config())
        # 不同参数、同工具连续失败
        d1 = ctl.after_call("web_search", '{"q": "1"}', _ERROR_RESULT)
        assert d1.action == "allow"
        d2 = ctl.after_call("web_search", '{"q": "2"}', _ERROR_RESULT)
        assert d2.action == "warn"
        assert d2.reason == "same_tool_failure_warning"
        d3 = ctl.after_call("web_search", '{"q": "3"}', _ERROR_RESULT)
        assert d3.action == "warn"
        d4 = ctl.after_call("web_search", '{"q": "4"}', _ERROR_RESULT)
        assert d4.action == "halt"
        assert ctl.halt_decision is not None

    def test_success_resets_failure_counts(self) -> None:
        ctl = GuardrailController(_config())
        args = '{"q": "x"}'
        ctl.after_call("do_thing", args, _ERROR_RESULT)
        ctl.after_call("do_thing", args, _OK_RESULT)
        # 再次失败从 1 重新计数
        d = ctl.after_call("do_thing", args, _ERROR_RESULT)
        assert d.action == "allow"

    def test_no_progress_warn_for_idempotent(self) -> None:
        ctl = GuardrailController(_config())
        args = '{"id": 1}'
        result = json.dumps({"data": [1, 2, 3]})
        ctl.after_call("get_user", args, result)
        d = ctl.after_call("get_user", args, result)
        assert d.action == "warn"
        assert d.reason == "idempotent_no_progress_warning"

    def test_no_progress_exempt_for_polling_tool(self) -> None:
        """check_background_tasks 是被系统提示鼓励的轮询工具，同结果不告警。"""
        ctl = GuardrailController(_config())
        args = "{}"
        result = json.dumps({"running": [], "completed": []})
        for _ in range(4):
            d = ctl.after_call("check_background_tasks", args, result)
            assert d.action == "allow"

    def test_no_progress_not_triggered_by_different_results(self) -> None:
        ctl = GuardrailController(_config())
        args = '{"id": 1}'
        ctl.after_call("get_user", args, '{"data": 1}')
        d = ctl.after_call("get_user", args, '{"data": 2}')
        assert d.action == "allow"

    def test_mutating_tool_no_progress_ignored(self) -> None:
        ctl = GuardrailController(_config())
        args = '{"x": 1}'
        ctl.after_call("send_message", args, '{"ok": true}')
        d = ctl.after_call("send_message", args, '{"ok": true}')
        assert d.action == "allow"

    def test_before_call_block_requires_hard_stop(self) -> None:
        ctl = GuardrailController(_config(hard_stop_enabled=False))
        args = '{"q": "x"}'
        for _ in range(5):
            ctl.after_call("web_search", args, _ERROR_RESULT)
        assert ctl.before_call("web_search", args).action == "allow"

        ctl_hard = GuardrailController(_config(hard_stop_enabled=True))
        for _ in range(3):
            ctl_hard.after_call("web_search", args, _ERROR_RESULT)
        d = ctl_hard.before_call("web_search", args)
        assert d.action == "block"
        assert d.reason == "repeated_exact_failure_block"

    def test_disabled_guardrail_allows_everything(self) -> None:
        ctl = GuardrailController(_config(enabled=False))
        for _ in range(10):
            d = ctl.after_call("web_search", '{"q": "x"}', _ERROR_RESULT)
            assert d.action == "allow"

    def test_reset(self) -> None:
        ctl = GuardrailController(_config())
        ctl.after_call("web_search", '{"q": "x"}', _ERROR_RESULT)
        ctl.reset()
        assert ctl.halt_decision is None
        d = ctl.after_call("web_search", '{"q": "x"}', _ERROR_RESULT)
        assert d.action == "allow"


class TestDecisionApplication:
    def test_append_guidance(self) -> None:
        ctl = GuardrailController(_config())
        ctl.after_call("web_search", '{"q": "x"}', _ERROR_RESULT)
        d = ctl.after_call("web_search", '{"q": "x"}', _ERROR_RESULT)
        combined = append_guardrail_guidance(_ERROR_RESULT, d)
        assert "工具守卫警告" in combined
        assert _ERROR_RESULT in combined

    def test_synthetic_block_result(self) -> None:
        ctl = GuardrailController(_config(hard_stop_enabled=True))
        args = '{"q": "x"}'
        for _ in range(3):
            ctl.after_call("web_search", args, _ERROR_RESULT)
        d = ctl.before_call("web_search", args)
        result = json.loads(synthetic_block_result(d))
        assert "error" in result
        assert result["guardrail"]["tool"] == "web_search"


class TestIdempotentHeuristic:
    def test_readonly_prefixes(self) -> None:
        assert is_idempotent_tool("get_entity_profile")
        assert is_idempotent_tool("search_skills")
        assert is_idempotent_tool("recall")
        assert not is_idempotent_tool("send_message")
        assert not is_idempotent_tool("memorize")


class TestGraduatedReminder:
    """分级提醒文案（对齐 dsh repeat-tool-reminder）：首次达阈温和提示，
    之后给出完整报告（工具名 + 连续次数 + 有界参数预览）。"""

    def test_first_warn_is_gentle(self) -> None:
        ctl = GuardrailController(_config(exact_failure_warn_after=2))
        args = '{"path": "/a"}'
        ctl.after_call("read_file", args, _ERROR_RESULT)
        d = ctl.after_call("read_file", args, _ERROR_RESULT)  # 第 2 次达阈
        assert d.action == "warn"
        assert "没有取得进展" in d.message
        # 首次温和提示不带详细报告（无次数/参数清单）
        assert "连续失败次数" not in d.message
        assert "重复的工具调用" not in d.message

    def test_subsequent_warn_has_full_report(self) -> None:
        ctl = GuardrailController(_config(exact_failure_warn_after=2))
        args = '{"path": "/a"}'
        for _ in range(2):
            ctl.after_call("read_file", args, _ERROR_RESULT)
        d = ctl.after_call("read_file", args, _ERROR_RESULT)  # 第 3 次，超阈
        assert d.action == "warn"
        assert "重复的工具调用" in d.message
        assert "连续失败次数: 3" in d.message
        assert "/a" in d.message  # 参数预览在场
        assert "请勿再以完全相同的参数调用" in d.message

    def test_args_preview_caps_long_arguments(self) -> None:
        from agent.mind.guardrails import _ARGS_PREVIEW_CHARS, _args_preview
        long_args = '{"data": "' + "x" * 2000 + '"}'
        preview = _args_preview(long_args)
        assert len(preview) <= _ARGS_PREVIEW_CHARS + 1  # +1 省略号
        assert preview.endswith("…")

    def test_args_preview_short_verbatim(self) -> None:
        from agent.mind.guardrails import _args_preview
        assert _args_preview('{"a": 1}') == '{"a":1}'


class TestGuardrailReset:
    """reset 清空全部计数（用户插话重置链场景调用）。"""

    def test_reset_clears_failure_counts(self) -> None:
        ctl = GuardrailController(_config(exact_failure_warn_after=2))
        args = '{"q": "x"}'
        ctl.after_call("web_search", args, _ERROR_RESULT)
        ctl.after_call("web_search", args, _ERROR_RESULT)  # 达 warn 阈
        ctl.reset()
        # 重置后重新从 0 计数：第 1 次失败不触发 warn
        d = ctl.after_call("web_search", args, _ERROR_RESULT)
        assert d.action == "allow"
