"""会话中断注册表的登记、消费与隔离。"""

from __future__ import annotations

import asyncio

from agent.mind.interrupt import InterruptRegistry


class TestInterruptRegistry:
    async def test_shutdown_interrupts_all_scopes_and_waits_for_records(self) -> None:
        reg = InterruptRegistry()
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        saved: list[str] = []

        async def execute(scope: str) -> None:
            nonlocal count
            with reg.execution(scope, scope):
                count += 1
                if count == 2:
                    entered.set()
                await reg.event(scope).wait()
                assert reg.consume(scope) == "服务正在关闭或重启"
                await release.wait()
                saved.append(scope)

        tasks = [asyncio.create_task(execute(scope)) for scope in ("conversation", "reflect:test")]
        await entered.wait()
        reg.quiesce()
        drain = asyncio.create_task(reg.drain())
        await asyncio.sleep(0)
        assert not drain.done()
        release.set()
        await asyncio.gather(*tasks, drain)
        assert set(saved) == {"conversation", "reflect:test"}
        with reg.execution("late", "late"):
            assert reg.event("late").is_set()
            assert reg.is_requested("late")
        assert reg.pending_scopes() == []

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
