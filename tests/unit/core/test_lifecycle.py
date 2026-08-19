"""Lifecycle 单例生命周期注册表单元测试。"""
from __future__ import annotations

import asyncio

import pytest

from core.lifecycle import Lifecycle


@pytest.fixture(autouse=True)
def _clean_registry():
    """用例前后复位，隔离注册表全局副作用。"""
    Lifecycle.reset()
    yield
    Lifecycle.reset()


def test_register_and_get():
    Lifecycle.register("alpha", object())
    assert Lifecycle.get("alpha") is not None
    assert Lifecycle.get("missing") is None


async def test_shutdown_runs_cleanups_in_reverse_order():
    cleaned: list[str] = []
    Lifecycle.register("first", None, cleanup=lambda: cleaned.append("first"))
    Lifecycle.register("second", None, cleanup=lambda: cleaned.append("second"))

    await Lifecycle.shutdown_all()
    assert cleaned == ["second", "first"]


async def test_same_name_registration_replaces_old_cleanup():
    cleaned: list[str] = []
    Lifecycle.register("svc", None, cleanup=lambda: cleaned.append("old"))
    Lifecycle.register("svc", None, cleanup=lambda: cleaned.append("new"))

    await Lifecycle.shutdown_all()
    assert cleaned == ["new"]


async def test_cleanup_timeout_degrades_to_log_and_continues():
    cleaned: list[str] = []

    async def stuck() -> None:
        await asyncio.sleep(5)

    Lifecycle.register("stuck", None, cleanup=stuck)
    Lifecycle.register("after", None, cleanup=lambda: cleaned.append("after"))

    await Lifecycle.shutdown_all(per_timeout=0.05)
    # stuck 超时跳过（先注册后清理），after 先执行不受影响
    assert cleaned == ["after"]


async def test_cleanup_failure_does_not_break_remaining():
    cleaned: list[str] = []

    def boom() -> None:
        raise RuntimeError("cleanup failed")

    Lifecycle.register("bad", None, cleanup=boom)
    Lifecycle.register("good", None, cleanup=lambda: cleaned.append("good"))

    await Lifecycle.shutdown_all()
    assert cleaned == ["good"]


async def test_start_all_runs_on_start_hooks_in_order():
    started: list[str] = []
    Lifecycle.register("a", None, on_start=lambda: started.append("a"))
    Lifecycle.register("b", None, on_start=lambda: started.append("b"))

    await Lifecycle.start_all()
    assert started == ["a", "b"]


async def test_tick_all_runs_tick_hooks():
    ticks: list[str] = []
    Lifecycle.register("t", None, on_tick=lambda: ticks.append("t"))

    await Lifecycle.tick_all()
    assert ticks == ["t"]


def test_snapshot_reflects_registration_order_and_hooks():
    Lifecycle.register("one", "inst", cleanup=lambda: None)
    Lifecycle.register("two", None, on_start=lambda: None, on_tick=lambda: None)

    snapshot = Lifecycle.snapshot()
    assert [s["name"] for s in snapshot] == ["one", "two"]
    assert [s["order"] for s in snapshot] == [1, 2]
    assert snapshot[0]["instance_type"] == "str"
    assert snapshot[0]["has_cleanup"] and not snapshot[0]["has_on_start"]
    assert snapshot[1]["has_on_start"] and snapshot[1]["has_on_tick"]


def test_request_shutdown_marks_restart_and_invokes_requester():
    called: list[bool] = []
    Lifecycle.set_shutdown_requester(lambda: called.append(True))

    Lifecycle.request_shutdown(restart=True)
    assert called == [True]
    assert Lifecycle.restart_requested()
