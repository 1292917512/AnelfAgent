"""ladybug native 监督（agent.memory.cognee.ladybug_guard）单元测试。

核心不变量：native 执行串行（wait_for 取消的孤儿查询不占锁让位）；
close/delete_graph 的门闸等待不在事件循环上；失控持有经 restart 阶梯
升级且每轮只升级一次；事件循环上的同步拆除有界（超时抛错而非冻结）。
刻意不做 native 中止（set_query_timeout/interrupt 对卡死扫描不可靠）。
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import pytest

from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.ladybug_guard import (
    _apply_guard,
    _patch_wal_recovery,
    _watchdog_check,
    native_restart_port,
)


class _FakeAdapterBase:
    """LadybugAdapter 最小替身：submit 直投线程池；close/delete_graph 复刻
    cognee 本地模式在调用线程同步拆除的行为。"""

    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.drop_thread_ids: list[int] = []
        self.closed = False
        self.graph_deleted = False

    def _submit_to_executor_locked(self, fn: Any, *args: Any) -> Any:
        return self.executor.submit(fn, *args)

    def _drop_native_resources(self) -> None:
        self.drop_thread_ids.append(threading.get_ident())

    async def close(self) -> None:
        self._drop_native_resources()
        self.closed = True

    async def delete_graph(self) -> None:
        self._drop_native_resources()
        self.graph_deleted = True


def _config(**overrides: Any) -> CogneeConfig:
    """小阈值测试配置（跳过 normalized 以便快进；钳制由独立用例覆盖）。"""
    values: dict[str, Any] = dict(native_watchdog_restart_seconds=0.2)
    values.update(overrides)
    return CogneeConfig(**values)


def _make_adapter_cls(config: Optional[CogneeConfig] = None) -> type:
    """每组用例独立的替身类（独立的门闸与看门狗状态，互不耦合）。"""
    cls = type("FakeAdapter", (_FakeAdapterBase,), {})
    cfg = config if config is not None else _config()
    assert _apply_guard(cls, lambda: cfg, watchdog_interval=3600) is True
    return cls


def _hold_gate(adapter: Any) -> tuple[threading.Event, threading.Event]:
    """提交一段占住门闸的 native 执行，返回 (已开始, 放行) 事件对。"""
    started = threading.Event()
    release = threading.Event()

    def native() -> None:
        started.set()
        release.wait(timeout=10)

    adapter._submit_to_executor_locked(native)
    assert started.wait(timeout=5)
    return started, release


class TestGate:
    def test_install_is_idempotent(self) -> None:
        cls = _make_adapter_cls()
        assert _apply_guard(cls, lambda: _config(), watchdog_interval=3600) is False

    def test_submit_returns_result(self) -> None:
        adapter = _make_adapter_cls()()
        future = adapter._submit_to_executor_locked(lambda: 41 + 1)
        assert future.result(timeout=5) == 42

    def test_two_submits_serialize(self) -> None:
        """并发提交的两段 native 执行不得在时间上重叠。"""
        adapter = _make_adapter_cls()()
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
        adapter = _make_adapter_cls()()
        _started, release = _hold_gate(adapter)
        drop_done_at: list[float] = []

        drop_thread = threading.Thread(
            target=lambda: (adapter._drop_native_resources(), drop_done_at.append(time.monotonic())),
        )
        drop_thread.start()
        time.sleep(0.2)
        assert not adapter.drop_thread_ids  # native 未结束，drop 不得完成
        release.set()
        drop_thread.join(timeout=5)
        assert adapter.drop_thread_ids
        assert drop_done_at

    async def test_cancelled_waiter_keeps_gate_until_native_done(self) -> None:
        """wait_for 超时取消协程后，孤儿 native 未结束前不得放行下一条查询。"""
        adapter = _make_adapter_cls()()
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


class TestLoopSafeTeardown:
    async def test_close_waits_off_event_loop(self) -> None:
        """close 的门闸等待发生在 worker 线程，事件循环保持响应。"""
        adapter = _make_adapter_cls()()
        _started, release = _hold_gate(adapter)
        loop_ident = threading.get_ident()

        close_task = asyncio.create_task(adapter.close())
        await asyncio.sleep(0.2)
        assert not close_task.done()  # 门闸被占，close 等待中

        # 事件循环仍然响应其它协程（冻结时本探针永远跑不到）
        probed: list[bool] = []

        async def probe() -> None:
            probed.append(True)

        await asyncio.wait_for(asyncio.create_task(probe()), timeout=1)
        assert probed == [True]

        release.set()
        await asyncio.wait_for(close_task, timeout=5)
        assert adapter.closed
        # 首次拆除（预拆除）必须发生在 worker 线程而非事件循环线程
        assert adapter.drop_thread_ids
        assert adapter.drop_thread_ids[0] != loop_ident

    async def test_delete_graph_waits_off_event_loop(self) -> None:
        adapter = _make_adapter_cls()()
        _started, release = _hold_gate(adapter)

        delete_task = asyncio.create_task(adapter.delete_graph())
        await asyncio.sleep(0.2)
        assert not delete_task.done()
        release.set()
        await asyncio.wait_for(delete_task, timeout=5)
        assert adapter.graph_deleted


class TestWatchdog:
    def test_restart_ladder_via_port(self) -> None:
        """门闸久持超阈值：经重启端口升级一次，同轮持有不重复升级。"""
        config = _config(native_watchdog_restart_seconds=0.1)
        cls = _make_adapter_cls(config)
        adapter = cls()
        calls: list[dict[str, Any]] = []

        def fake_request(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"ok": True, "restarting": True}

        _started, release = _hold_gate(adapter)
        state = cls._anel_guard_state
        native_restart_port.set(fake_request)
        try:
            time.sleep(0.15)  # 越过 restart 阈值
            _watchdog_check(state)
            assert len(calls) == 1
            assert calls[0]["source"] == "cognee_native_watchdog"

            _watchdog_check(state)  # 同一轮持有不重复升级
            assert len(calls) == 1
        finally:
            native_restart_port.unbind()
            release.set()

    def test_watchdog_disabled_takes_no_action(self) -> None:
        config = _config(native_watchdog_enabled=False, native_watchdog_restart_seconds=0.1)
        cls = _make_adapter_cls(config)
        adapter = cls()
        calls: list[dict[str, Any]] = []

        def fake_request(**kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {"ok": True}

        _started, release = _hold_gate(adapter)
        native_restart_port.set(fake_request)
        try:
            time.sleep(0.15)  # 越过阈值
            _watchdog_check(cls._anel_guard_state)
            assert calls == []
        finally:
            native_restart_port.unbind()
            release.set()

    def test_restart_without_bound_port_is_log_only(self) -> None:
        """端口未施绑/施绑 None：失控升级只告警，不做进程动作、不抛错。"""
        config = _config(native_watchdog_restart_seconds=0.1)
        cls = _make_adapter_cls(config)
        adapter = cls()
        _started, release = _hold_gate(adapter)
        was_bound = native_restart_port.bound
        if was_bound:
            saved = native_restart_port.get()
        native_restart_port.unbind()
        try:
            time.sleep(0.15)
            _watchdog_check(cls._anel_guard_state)  # 不抛错即降级成功
            native_restart_port.set(None)
            _watchdog_check(cls._anel_guard_state)
        finally:
            native_restart_port.unbind()
            if was_bound:
                native_restart_port.set(saved)
            release.set()

    def test_hold_released_resets_escalation(self) -> None:
        """持有正常结束后台账清空，看门狗无动作。"""
        cls = _make_adapter_cls(_config(native_watchdog_restart_seconds=0.1))
        adapter = cls()
        future = adapter._submit_to_executor_locked(lambda: 1)
        assert future.result(timeout=5) == 1
        assert cls._anel_guard_state.current_hold() is None

    async def test_loop_thread_drop_is_bounded(self) -> None:
        """事件循环线程上的同步拆除有界：门闸久持时抛错而非冻结。"""
        config = _config(native_watchdog_restart_seconds=0.2)
        adapter = _make_adapter_cls(config)()
        _started, release = _hold_gate(adapter)
        began = time.monotonic()
        try:
            with pytest.raises(RuntimeError, match="门闸被持有"):
                adapter._drop_native_resources()
            assert time.monotonic() - began < 5
        finally:
            release.set()

    def test_worker_thread_drop_waits_unbounded(self) -> None:
        """worker 线程上的拆除不受有界逻辑影响，照常等门闸释放。"""
        adapter = _make_adapter_cls()()
        _started, release = _hold_gate(adapter)
        done: list[bool] = []
        drop_thread = threading.Thread(
            target=lambda: (adapter._drop_native_resources(), done.append(True)),
        )
        drop_thread.start()
        time.sleep(0.2)
        assert not done
        release.set()
        drop_thread.join(timeout=5)
        assert done == [True]


class TestWalRecovery:
    def test_defaults_to_tolerant_and_idempotent(self) -> None:
        """WAL 容错：默认注入 throw_on_wal_replay_failure=False 且幂等。"""

        class _FakeDatabase:
            def __init__(self, database_path: Any = None, **kwargs: Any) -> None:
                self.database_path = database_path
                self.throw_on_wal_replay_failure = kwargs.get(
                    "throw_on_wal_replay_failure",
                    True,
                )

        class _FakeAdapterModule:
            Database = _FakeDatabase

        _patch_wal_recovery(_FakeAdapterModule)
        patched = _FakeAdapterModule.Database

        assert patched is not _FakeDatabase
        assert getattr(patched, "_anel_wal_tolerant", False)
        assert issubclass(patched, _FakeDatabase)
        assert patched("/tmp/x.lbug").throw_on_wal_replay_failure is False
        explicit = patched("/tmp/x.lbug", throw_on_wal_replay_failure=True)
        assert explicit.throw_on_wal_replay_failure is True

        _patch_wal_recovery(_FakeAdapterModule)
        assert _FakeAdapterModule.Database is patched


class TestConfigNormalization:
    def test_watchdog_restart_threshold_floor(self) -> None:
        config = CogneeConfig(native_watchdog_restart_seconds=5.0).normalized()
        assert config.native_watchdog_restart_seconds == 120.0
