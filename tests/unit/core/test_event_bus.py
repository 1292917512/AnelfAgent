"""EventBus（core.event_bus）单元测试。

直测三处微妙语义：once 清理必须作用于当前列表而非发射前的旧快照、
timeout 的 0/None/正数三态、_same_handler 的绑定方法比较。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

import pytest

from core.event_bus import EventBus


class _Panel:
    """提供绑定方法订阅源的测试夹具。"""

    def __init__(self) -> None:
        self.hits = 0

    async def handle(self, payload: Dict[str, Any]) -> None:
        self.hits += 1


# ------------------------------------------------------------------
# once 清理
# ------------------------------------------------------------------


async def test_once_subscription_fires_exactly_once() -> None:
    bus = EventBus()
    calls: list[str] = []

    async def once_handler(payload: Dict[str, Any]) -> None:
        calls.append("once")

    bus.on("evt", once_handler, once=True)
    await bus.emit("evt", {})
    await bus.emit("evt", {})
    assert calls == ["once"]
    assert not bus.has_listeners("evt")


async def test_once_cleanup_targets_current_list_not_stale_snapshot() -> None:
    """处理器执行期间 off() 整体替换列表对象后，once 清理仍须作用于新列表。

    若清理作用在发射前抓到的旧列表引用上，once 订阅会残留并在下一次 emit 再次触发。
    """
    bus = EventBus()
    calls: list[str] = []

    async def bystander(payload: Dict[str, Any]) -> None:
        calls.append("bystander")

    async def once_handler(payload: Dict[str, Any]) -> None:
        calls.append("once")
        bus.off("evt", bystander)

    bus.on("evt", once_handler, once=True)
    bus.on("evt", bystander)
    await bus.emit("evt", {})
    await bus.emit("evt", {})
    assert calls == ["once", "bystander"]


async def test_subscription_added_during_emit_skips_inflight_batch() -> None:
    """emit 按订阅快照触发，执行期间新增的订阅不在本批被调用。"""
    bus = EventBus()
    calls: list[str] = []

    async def late_handler(payload: Dict[str, Any]) -> None:
        calls.append("late")

    async def starter(payload: Dict[str, Any]) -> None:
        calls.append("starter")
        bus.on("evt", late_handler)

    bus.on("evt", starter)
    await bus.emit("evt", {})
    assert calls == ["starter"]
    await bus.emit("evt", {})
    assert calls == ["starter", "starter", "late"]


# ------------------------------------------------------------------
# timeout 三态
# ------------------------------------------------------------------


async def test_timeout_zero_falls_back_to_bus_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """timeout=0（默认）：套用总线默认时限，超时处理器被取消。"""
    monkeypatch.setattr("core.event_bus.HANDLER_TIMEOUT_SECONDS", 0.05)
    bus = EventBus()
    done = False

    async def slow(payload: Dict[str, Any]) -> None:
        nonlocal done
        await asyncio.sleep(1.0)
        done = True

    bus.on("evt", slow)
    started = time.monotonic()
    await bus.emit("evt", {})
    assert not done
    assert time.monotonic() - started < 0.9


async def test_timeout_none_has_no_bus_level_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """timeout=None：总线不设时限，慢于总线默认值的处理器也能完成。"""
    monkeypatch.setattr("core.event_bus.HANDLER_TIMEOUT_SECONDS", 0.05)
    bus = EventBus()
    done = False

    async def patient(payload: Dict[str, Any]) -> None:
        nonlocal done
        await asyncio.sleep(0.15)
        done = True

    bus.on("evt", patient, timeout=None)
    await bus.emit("evt", {})
    assert done


async def test_positive_timeout_overrides_bus_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """timeout=正数：独立时限替换总线默认值。"""
    monkeypatch.setattr("core.event_bus.HANDLER_TIMEOUT_SECONDS", 10.0)
    bus = EventBus()
    done = False

    async def slow(payload: Dict[str, Any]) -> None:
        nonlocal done
        await asyncio.sleep(1.0)
        done = True

    bus.on("evt", slow, timeout=0.05)
    started = time.monotonic()
    await bus.emit("evt", {})
    assert not done
    assert time.monotonic() - started < 0.9


async def test_timeout_and_exception_are_isolated() -> None:
    """单个处理器超时或异常只记 WARNING，不影响同事件其他处理器。"""
    bus = EventBus()
    flags: list[str] = []

    async def hangs(payload: Dict[str, Any]) -> None:
        await asyncio.sleep(1.0)

    async def broken(payload: Dict[str, Any]) -> None:
        raise RuntimeError("boom")

    async def healthy(payload: Dict[str, Any]) -> None:
        flags.append("healthy")

    bus.on("evt", hangs, timeout=0.05)
    bus.on("evt", broken)
    bus.on("evt", healthy)
    started = time.monotonic()
    await bus.emit("evt", {})
    assert flags == ["healthy"]
    assert time.monotonic() - started < 0.9


# ------------------------------------------------------------------
# _same_handler 绑定方法比较
# ------------------------------------------------------------------


async def test_off_removes_bound_method_via_fresh_accessor() -> None:
    """off() 传入重新访问属性得到的新绑定方法对象，仍能命中原订阅。"""
    bus = EventBus()
    panel = _Panel()
    bus.on("evt", panel.handle)
    await bus.emit("evt", {})
    assert panel.hits == 1
    assert bus.off("evt", panel.handle) is True
    await bus.emit("evt", {})
    assert panel.hits == 1


async def test_off_ignores_same_method_of_other_instance() -> None:
    """同名方法绑定在不同实例上不构成同一处理器。"""
    bus = EventBus()
    owner, outsider = _Panel(), _Panel()
    bus.on("evt", owner.handle)
    assert bus.off("evt", outsider.handle) is False
    await bus.emit("evt", {})
    assert owner.hits == 1
    assert outsider.hits == 0


def test_off_plain_function_and_unknown_event() -> None:
    bus = EventBus()

    async def fn(payload: Dict[str, Any]) -> None:
        return None

    bus.on("evt", fn)
    assert bus.off("evt", fn) is True
    assert bus.off("evt", fn) is False
    assert bus.off("missing", fn) is False
