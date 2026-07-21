"""内部消息契约与发送边界规整层（agent.mind.message_schema）单元测试。

发送边界规则统一收拢于 normalize_for_send：
- normalize_roles：头部连续 system 块保持，中途 system 注入转 user
- fix_trailing_assistant：尾部 assistant 转 user（Anthropic prefill 400）
"""

from __future__ import annotations

import pytest

from agent.mind.message_schema import (
    ChatMessage,
    fix_trailing_assistant,
    normalize_for_send,
    normalize_roles,
    validate_messages,
)


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
