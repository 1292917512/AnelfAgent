"""会话中断注册表的登记、消费与隔离。"""

from __future__ import annotations

from agent.mind.interrupt import InterruptRegistry


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
