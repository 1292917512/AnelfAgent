"""ladybug native 串行门（agent.memory.cognee.client._apply_native_gate）单元测试。

覆盖 2026-08 SIGSEGV 修复的核心不变量：native 执行的临界区由执行线程持有，
协程被取消（wait_for 超时）不影响锁的释放时机；拆除资源（drop）必须等待
在途 native 执行结束，不能与孤儿执行重叠。
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from agent.memory.cognee.client import _apply_native_gate


class _FakeAdapter:
    """LadybugAdapter 最小替身：submit 直投线程池，drop 仅记录时序。"""

    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.dropped = False

    def _submit_to_executor_locked(self, fn: Any, *args: Any) -> Any:
        return self.executor.submit(fn, *args)

    def _drop_native_resources(self) -> None:
        self.dropped = True


# 类级补丁只安装一次（幂等），所有用例共享同一把门
assert _apply_native_gate(_FakeAdapter) is True


class TestNativeGate:
    def test_install_is_idempotent(self) -> None:
        assert _apply_native_gate(_FakeAdapter) is False

    def test_submit_returns_result(self) -> None:
        adapter = _FakeAdapter()
        future = adapter._submit_to_executor_locked(lambda: 41 + 1)
        assert future.result(timeout=5) == 42

    def test_two_submits_serialize(self) -> None:
        """并发提交的两段 native 执行不得在时间上重叠。"""
        adapter = _FakeAdapter()
        timestamps: dict[str, float] = {}

        def first() -> None:
            timestamps["first_start"] = time.monotonic()
            time.sleep(0.3)
            timestamps["first_end"] = time.monotonic()

        def second() -> None:
            timestamps["second_start"] = time.monotonic()

        future_first = adapter._submit_to_executor_locked(first)
        time.sleep(0.05)  # 确保 first 先取得门
        future_second = adapter._submit_to_executor_locked(second)
        future_first.result(timeout=5)
        future_second.result(timeout=5)
        assert timestamps["second_start"] >= timestamps["first_end"]

    def test_drop_waits_for_inflight_native(self) -> None:
        """drop 必须等在途 native 执行结束（防扫描中途句柄被拆）。"""
        adapter = _FakeAdapter()
        started = threading.Event()
        release = threading.Event()
        native_done_at: list[float] = []
        drop_done_at: list[float] = []

        def native() -> None:
            started.set()
            release.wait(timeout=5)
            native_done_at.append(time.monotonic())

        adapter._submit_to_executor_locked(native)
        assert started.wait(timeout=5)

        drop_thread = threading.Thread(
            target=lambda: (adapter._drop_native_resources(), drop_done_at.append(time.monotonic())),
        )
        drop_thread.start()
        time.sleep(0.2)
        assert not adapter.dropped  # native 未结束，drop 不得完成
        release.set()
        drop_thread.join(timeout=5)
        assert adapter.dropped
        assert native_done_at and drop_done_at
        assert drop_done_at[0] >= native_done_at[0]

    async def test_cancelled_waiter_keeps_gate_until_native_done(self) -> None:
        """wait_for 超时取消协程后，孤儿 native 未结束前不得放行下一条查询。"""
        adapter = _FakeAdapter()
        release = threading.Event()
        order: list[str] = []

        def orphan_native() -> None:
            release.wait(timeout=5)
            order.append("orphan_done")

        async def timed_out_query() -> None:
            future = adapter._submit_to_executor_locked(orphan_native)
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=0.1)

        with pytest.raises(asyncio.TimeoutError):
            await timed_out_query()

        async def next_query() -> None:
            future = adapter._submit_to_executor_locked(lambda: order.append("next_done"))
            await asyncio.wrap_future(future)

        next_task = asyncio.create_task(next_query())
        await asyncio.sleep(0.2)
        assert order == []  # 孤儿仍在跑，下一条查询必须排队
        release.set()
        await asyncio.wait_for(next_task, timeout=5)
        assert order == ["orphan_done", "next_done"]
