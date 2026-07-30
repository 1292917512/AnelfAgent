"""内部消息契约与发送边界规整层（agent.mind.message_schema）单元测试。

发送边界规则统一收拢于 normalize_for_send：
- normalize_roles：头部连续 system 块保持，中途 system 注入转 user
- fix_trailing_assistant：尾部 assistant 转 user（Anthropic prefill 400）
"""

from __future__ import annotations

import pytest

from agent.mind.message_schema import (
    ChatMessage,
    ensure_tool_result_pairing,
    fix_empty_tool_call_content,
    fix_trailing_assistant,
    normalize_for_send,
    normalize_roles,
    validate_messages,
)
from agent.mind.mind import _normalize_message_roles


class TestChatMessage:
    def test_construct_and_dump(self) -> None:
        msg = ChatMessage.user("你好")
        assert msg.to_dict() == {"role": "user", "content": "你好"}

    def test_tool_result_helper(self) -> None:
        msg = ChatMessage.tool_result("call_1", "结果")
        dumped = msg.to_dict()
        assert dumped["role"] == "tool"
        assert dumped["tool_call_id"] == "call_1"

    def test_extra_fields_passthrough(self) -> None:
        """提供商扩展字段（cache_control / reasoning_details 等）透传。"""
        msg = ChatMessage.model_validate({
            "role": "system",
            "content": "提示词",
            "cache_control": {"type": "ephemeral"},
        })
        assert msg.to_dict()["cache_control"] == {"type": "ephemeral"}

    def test_validate_messages(self) -> None:
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        assert validate_messages(msgs) == msgs
        with pytest.raises(Exception):
            validate_messages([{"content": "缺 role"}])


class TestNormalizeRoles:
    def test_head_system_preserved(self) -> None:
        msgs = [
            {"role": "system", "content": "人设"},
            {"role": "system", "content": "便签"},
            {"role": "user", "content": "你好"},
        ]
        result = normalize_roles(msgs)
        assert [m["role"] for m in result] == ["system", "system", "user"]

    def test_mid_system_becomes_user(self) -> None:
        msgs = [
            {"role": "system", "content": "提示词"},
            {"role": "user", "content": "你好"},
            {"role": "system", "content": "[系统提示] 纠正"},
        ]
        result = normalize_roles(msgs)
        assert [m["role"] for m in result] == ["system", "user", "user"]
        assert result[2]["content"] == "[系统提示] 纠正"

    def test_extra_keys_kept_on_conversion(self) -> None:
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "system", "content": "反馈", "custom": 1},
        ]
        result = normalize_roles(msgs)
        assert result[1] == {"role": "user", "content": "反馈", "custom": 1}


class TestFixTrailingAssistant:
    def test_trailing_assistant_converted(self) -> None:
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        result = fix_trailing_assistant(msgs)
        assert result[-1]["role"] == "user"
        assert result[-1]["content"] == "a"

    def test_trailing_user_untouched(self) -> None:
        msgs = [{"role": "user", "content": "q"}]
        assert fix_trailing_assistant(msgs)[-1]["role"] == "user"

    def test_trailing_system_skipped_to_last_non_system(self) -> None:
        """末尾 system 不参与判断，向前找最后一条非 system 消息。"""
        msgs = [
            {"role": "assistant", "content": "a"},
            {"role": "system", "content": "尾部提示"},
        ]
        result = fix_trailing_assistant(msgs)
        assert result[0]["role"] == "user"


class TestFixEmptyToolCallContent:
    def test_empty_content_with_tool_calls_becomes_none(self) -> None:
        """纯工具调用的 assistant 空 content 置 None，避免 anthropic 端点占位符污染。"""
        msgs = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function",
                                "function": {"name": "recall", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "结果"},
        ]
        result = fix_empty_tool_call_content(msgs)
        assert result[1]["content"] is None
        assert result[1]["tool_calls"]

    def test_whitespace_content_also_fixed(self) -> None:
        msgs = [{
            "role": "assistant", "content": "  \n",
            "tool_calls": [{"id": "c", "type": "function",
                            "function": {"name": "t", "arguments": "{}"}}],
        }]
        assert fix_empty_tool_call_content(msgs)[0]["content"] is None

    def test_non_empty_or_toolless_untouched(self) -> None:
        """有文本的 assistant、无 tool_calls 的空 assistant 均不动。"""
        msgs = [
            {"role": "assistant", "content": "有文本",
             "tool_calls": [{"id": "c", "type": "function",
                             "function": {"name": "t", "arguments": "{}"}}]},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": ""},
        ]
        result = fix_empty_tool_call_content(msgs)
        assert result[0]["content"] == "有文本"
        assert result[1]["content"] == ""
        assert result[2]["content"] == ""

    def test_original_dict_not_mutated(self) -> None:
        """修复产生新 dict，不污染调用方持有的原消息（tool_chain 内部复用）。"""
        original = {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c", "type": "function",
                            "function": {"name": "t", "arguments": "{}"}}],
        }
        result = fix_empty_tool_call_content([original])
        assert original["content"] == ""
        assert result[0] is not original


class TestNormalizeForSend:
    def test_combined_rules(self) -> None:
        """角色归一 + prefill 修复组合生效。"""
        msgs = [
            {"role": "system", "content": "人设"},
            {"role": "user", "content": "q"},
            {"role": "system", "content": "中途反馈"},
            {"role": "assistant", "content": "尾部独白"},
        ]
        result = normalize_for_send(msgs)
        assert [m["role"] for m in result] == ["system", "user", "user", "user"]

    def test_stable_prefix_byte_stable(self) -> None:
        """头部 system 块内容不变（Anthropic 前缀缓存复用的前提）。"""
        head = {"role": "system", "content": "稳定层", "cache_control": {"type": "ephemeral"}}
        msgs = [head, {"role": "user", "content": "q"}]
        result = normalize_for_send(msgs)
        assert result[0] == head


# ==================================================================
# tool_use/tool_result 配对铁律（发送边界最后一道防线）
# ==================================================================

def _assistant_with_calls(*ids: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "t", "arguments": "{}"}}
            for i in ids
        ],
    }


class TestPairingRepair:
    def test_missing_result_synthesized(self):
        messages = [
            {"role": "user", "content": "hi"},
            _assistant_with_calls("a", "b"),
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
        ]
        out = ensure_tool_result_pairing(messages)
        tool_msgs = [m for m in out if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        synth = [m for m in tool_msgs if m["tool_call_id"] == "b"][0]
        assert "中断" in synth["content"]
        # 合成结果紧跟 assistant 消息
        idx_assistant = next(i for i, m in enumerate(out) if m.get("tool_calls"))
        assert out[idx_assistant + 1]["tool_call_id"] in ("a", "b")

    def test_orphan_result_removed(self):
        messages = [
            _assistant_with_calls("a"),
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
            {"role": "tool", "tool_call_id": "ghost", "content": "???"},
        ]
        out = ensure_tool_result_pairing(messages)
        assert all(m.get("tool_call_id") != "ghost" for m in out)

    def test_complete_pairing_untouched(self):
        messages = [
            _assistant_with_calls("a"),
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
            {"role": "user", "content": "next"},
        ]
        out = ensure_tool_result_pairing(messages)
        assert out == messages

    def test_multiple_assistant_rounds(self):
        messages = [
            _assistant_with_calls("a"),
            {"role": "tool", "tool_call_id": "a", "content": "ok"},
            _assistant_with_calls("b", "c"),
            {"role": "tool", "tool_call_id": "c", "content": "ok"},
        ]
        out = ensure_tool_result_pairing(messages)
        ids = [m["tool_call_id"] for m in out if m["role"] == "tool"]
        assert sorted(ids) == ["a", "b", "c"]


class TestNormalizeForSendPairing:
    def test_pairing_repaired_before_send(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            _assistant_with_calls("a"),
        ]
        out = normalize_for_send(messages)
        assert any(m["role"] == "tool" and m["tool_call_id"] == "a" for m in out)


# ==================================================================
# 发送边界角色归一（mind._normalize_message_roles）
# ==================================================================

class TestNormalizeMessageRoles:
    def test_head_system_preserved(self) -> None:
        """头部连续 system 块（stable/context 提示词分层）保持不变。"""
        msgs = [
            {"role": "system", "content": "人设与工具提示"},
            {"role": "system", "content": "便签上下文"},
            {"role": "user", "content": "你好"},
        ]
        result = _normalize_message_roles(msgs)
        assert [m["role"] for m in result] == ["system", "system", "user"]

    def test_mid_conversation_system_becomes_user(self) -> None:
        """首个非 system 之后的 system 注入转为 user，内容与顺序不变。"""
        msgs = [
            {"role": "system", "content": "提示词"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "内心独白…"},
            {"role": "system", "content": "[系统提示] 你刚才的文字是内心独白"},
            {"role": "system", "content": "[系统] 执行上下文"},
        ]
        result = _normalize_message_roles(msgs)
        assert [m["role"] for m in result] == [
            "system", "user", "assistant", "user", "user",
        ]
        # 内容与顺序不变
        assert result[3]["content"] == "[系统提示] 你刚才的文字是内心独白"
        assert result[4]["content"] == "[系统] 执行上下文"

    def test_tool_messages_untouched(self) -> None:
        """assistant.tool_calls 与 tool 结果消息原样保留。"""
        msgs = [
            {"role": "system", "content": "提示词"},
            {"role": "user", "content": "画图"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "tc1"}]},
            {"role": "tool", "tool_call_id": "tc1", "content": "{}"},
            {"role": "system", "content": "[后台任务完成] …"},
        ]
        result = _normalize_message_roles(msgs)
        assert result[2]["tool_calls"] == [{"id": "tc1"}]
        assert result[3]["role"] == "tool"
        assert result[4]["role"] == "user"

    def test_history_meta_messages_become_user(self) -> None:
        """DB 历史中的元消息（[已执行操作摘要] 等）在发送边界转为 user。"""
        msgs = [
            {"role": "system", "content": "提示词"},
            {"role": "user", "content": "帮我搜索"},
            {"role": "assistant", "content": "好的"},
            {"role": "system", "content": "[已执行操作摘要] 本轮共执行 1 次工具"},
            {"role": "user", "content": "结果呢"},
        ]
        result = _normalize_message_roles(msgs)
        assert [m["role"] for m in result] == [
            "system", "user", "assistant", "user", "user",
        ]

    def test_input_not_mutated(self) -> None:
        """输入消息 dict 不被修改（转换产生新 dict）。"""
        original = {"role": "system", "content": "中途注入"}
        msgs = [{"role": "user", "content": "你好"}, original]
        result = _normalize_message_roles(msgs)
        assert original["role"] == "system"
        assert result[1] is not original

    def test_all_system_preserved(self) -> None:
        """全 system 列表（纯提示词场景）整体保持。"""
        msgs = [{"role": "system", "content": "a"}, {"role": "system", "content": "b"}]
        assert [m["role"] for m in _normalize_message_roles(msgs)] == ["system", "system"]

    def test_no_system_unchanged(self) -> None:
        msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        assert _normalize_message_roles(msgs) == msgs

    def test_empty_list(self) -> None:
        assert _normalize_message_roles([]) == []
