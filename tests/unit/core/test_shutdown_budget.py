"""关停全局预算与后置钩子单元测试（Application 关停序列硬化）。"""

from __future__ import annotations

import asyncio
import time

import pytest

from core.application import Application
from core.lifecycle import Lifecycle


@pytest.fixture(autouse=True)
def _clean_registry():
    Lifecycle.reset()
    yield
    Lifecycle.reset()


class TestShutdownBudget:
    async def test_deadline_trims_per_component_timeout(self):
        """剩余预算小于单项上限时按剩余预算裁剪：超时组件被截断，后续仍执行。"""
        ran: list[str] = []

        async def slow() -> None:
            await asyncio.sleep(10.0)

        async def fast() -> None:
            ran.append("fast")

        Lifecycle.register("slow", None, cleanup=slow)
        Lifecycle.register("fast", None, cleanup=fast)
        # 剩余预算 0.1s：slow 被截断，fast 仍被清理
        await Lifecycle.shutdown_all(deadline=time.monotonic() + 0.1)
        assert ran == ["fast"]

    async def test_exhausted_budget_skips_rest(self):
        """预算耗尽即跳过余下组件（记名不抛错），注册表照常清空。"""
        ran: list[str] = []

        async def never() -> None:
            ran.append("never")
            await asyncio.sleep(0)

        Lifecycle.register("never", None, cleanup=never)
        await Lifecycle.shutdown_all(deadline=time.monotonic() - 1.0)
        assert ran == []
        assert Lifecycle.snapshot() == []

    async def test_generous_budget_behaves_like_before(self):
        """预算充裕时行为与原全量清理一致。"""
        ran: list[str] = []

        async def quick() -> None:
            ran.append("quick")

        Lifecycle.register("quick", None, cleanup=quick)
        await Lifecycle.shutdown_all(deadline=time.monotonic() + 30.0)
        assert ran == ["quick"]


class TestPostShutdownHooks:
    async def test_post_hooks_run_after_shutdown_all(self):
        """后置钩子在全部组件清理之后执行（实例锁释放的时序保障）。"""
        order: list[str] = []
        app = Application()

        async def cleanup_a() -> None:
            order.append("cleanup")

        Lifecycle.register("a", None, cleanup=cleanup_a)
        app.on_post_shutdown("release", lambda: order.append("release"))
        await app._shutdown()
        assert order == ["cleanup", "release"]

    async def test_post_hook_failure_does_not_block_rest(self):
        order: list[str] = []
        app = Application()
        app.on_post_shutdown("boom", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        app.on_post_shutdown("ok", lambda: order.append("ok"))
        await app._shutdown()
        assert order == ["ok"]
