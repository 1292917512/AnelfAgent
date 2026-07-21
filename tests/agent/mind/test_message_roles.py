"""发送边界角色归一（mind._normalize_message_roles）单元测试。

anthropic 协议端点会把任意位置的 system 消息抽离到 system 参数顶部，
导致中途注入的纠正/反馈脱离上下文位置。归一规则：
头部连续 system 块（提示词分层）保持 system，之后的 system 消息转 user。
"""

from __future__ import annotations

from agent.mind.mind import _normalize_message_roles


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
