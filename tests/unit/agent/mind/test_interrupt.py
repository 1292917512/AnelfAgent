"""中断注册表与关键词识别（agent.mind.interrupt）单元测试。"""

from __future__ import annotations

from agent.mind.interrupt import (
    InterruptRegistry,
    match_interrupt_keyword,
)


class TestInterruptRegistry:
    def test_request_and_consume(self) -> None:
        reg = InterruptRegistry()
        assert not reg.is_requested("user_1")
        reg.request("user_1", reason="测试")
        assert reg.is_requested("user_1")
        assert reg.consume("user_1") == "测试"
        assert not reg.is_requested("user_1")

    def test_consume_empty_returns_none(self) -> None:
        reg = InterruptRegistry()
        assert reg.consume("nobody") is None

    def test_empty_scope_ignored(self) -> None:
        reg = InterruptRegistry()
        reg.request("")
        assert reg.pending_scopes() == []

    def test_scope_isolation(self) -> None:
        reg = InterruptRegistry()
        reg.request("user_1")
        assert not reg.is_requested("user_2")
        assert not reg.is_requested("group_1")

    def test_clear(self) -> None:
        reg = InterruptRegistry()
        reg.request("user_1")
        reg.clear("user_1")
        assert not reg.is_requested("user_1")

    def test_request_idempotent(self) -> None:
        reg = InterruptRegistry()
        reg.request("user_1", reason="第一次")
        reg.request("user_1", reason="第二次")
        assert reg.consume("user_1") == "第二次"


class TestKeywordMatch:
    def test_exact_chinese(self) -> None:
        assert match_interrupt_keyword("停止")
        assert match_interrupt_keyword("别说了")
        assert match_interrupt_keyword("停下")

    def test_exact_english_case_insensitive(self) -> None:
        assert match_interrupt_keyword("stop")
        assert match_interrupt_keyword("STOP")
        assert match_interrupt_keyword("Cancel")

    def test_punctuation_stripped(self) -> None:
        assert match_interrupt_keyword("停止。")
        assert match_interrupt_keyword("stop!")
        assert match_interrupt_keyword("  停止  ")

    def test_embedded_not_matched(self) -> None:
        """包含式不匹配：中断语义必须是整条消息的意图。"""
        assert not match_interrupt_keyword("请帮我分析停止损失的原因")
        assert not match_interrupt_keyword("别说了这个话题我们来聊点别的")
        assert not match_interrupt_keyword("stop loss 策略怎么设置")

    def test_too_long_not_matched(self) -> None:
        assert not match_interrupt_keyword("停止" * 10)

    def test_empty_not_matched(self) -> None:
        assert not match_interrupt_keyword("")
        assert not match_interrupt_keyword("   ")
