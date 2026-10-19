"""主思维 Driver 的单航班、唤醒合并、任务认领与关停测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

from agent.mind.driver import MindDriver


def _fake_mind() -> Any:
    claimed: set[str] = set()
    mind = SimpleNamespace(
        _reset_phase_if_idle=Mock(),
        _autonomous_cycle=AsyncMock(),
        pfc=SimpleNamespace(pending_analysis=[]),
    )

    def claim_task(name: str) -> tuple[bool, str]:
        if name in claimed:
            return False, "任务已在执行或排队中"
        claimed.add(name)
        return True, ""

    def release_task_claim(name: str) -> None:
        claimed.discard(name)

    engine = SimpleNamespace(
        tick=AsyncMock(return_value=[]),
        claim_task=claim_task,
        release_task_claim=release_task_claim,
        run_claimed_task=AsyncMock(return_value=None),
        claimed=claimed,
    )
    mind.heartbeat_engine = engine
    mind.driver = MindDriver(mind)
    return mind


async def test_cycle_requests_are_serial_and_latched() -> None:
    mind = _fake_mind()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls: list[bool] = []
    active = 0
    max_active = 0

    async def _cycle(*, is_heartbeat: bool) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append(is_heartbeat)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        active -= 1

    mind._autonomous_cycle = _cycle
    try:
        mind.driver.request_cycle()
        await entered.wait()
        mind.driver.request_cycle(is_heartbeat=True)
        mind.driver.request_cycle()
        release.set()
        await mind.driver.when_idle()
        assert calls == [False, False, True]
        assert max_active == 1
    finally:
        await mind.driver.close()


async def test_named_task_is_deduplicated_while_queued() -> None:
    mind = _fake_mind()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _run(name: str, *, trigger: str) -> str:
        entered.set()
        try:
            await release.wait()
            return "done"
        finally:
            mind.heartbeat_engine.release_task_claim(name)

    mind.heartbeat_engine.run_claimed_task = _run
    try:
        accepted, _, future = mind.driver.submit_task("a")
        assert accepted and future is not None
        duplicate, _, _ = mind.driver.submit_task("a")
        assert duplicate is False
        await entered.wait()
        release.set()
        assert await future == "done"
        assert mind.heartbeat_engine.claimed == set()
    finally:
        await mind.driver.close()


async def test_immediate_cycle_cancels_delayed_retry() -> None:
    mind = _fake_mind()
    try:
        mind.driver.request_cycle(delay=0.03)
        mind.driver.request_cycle()
        await mind.driver.when_idle()
        await asyncio.sleep(0.04)
        assert mind._autonomous_cycle.await_count == 1
    finally:
        await mind.driver.close()


async def test_tick_waiters_share_one_inflight_tick() -> None:
    mind = _fake_mind()
    mind.heartbeat_engine.tick = AsyncMock(return_value=["a"])
    try:
        first = mind.driver.submit_tick()
        second = mind.driver.submit_tick()
        assert await first == ["a"]
        assert await second == ["a"]
        assert mind.heartbeat_engine.tick.await_count == 1
    finally:
        await mind.driver.close()


async def test_close_releases_running_and_queued_task_claims() -> None:
    mind = _fake_mind()
    entered = asyncio.Event()
    never = asyncio.Event()

    async def _run(name: str, *, trigger: str) -> None:
        entered.set()
        try:
            await never.wait()
        finally:
            mind.heartbeat_engine.release_task_claim(name)

    mind.heartbeat_engine.run_claimed_task = _run
    accepted, _, running = mind.driver.submit_task("a")
    assert accepted and running is not None
    await entered.wait()
    accepted, _, queued = mind.driver.submit_task("b")
    assert accepted and queued is not None
    await mind.driver.close()

    assert running.cancelled()
    assert queued.cancelled()
    assert mind.heartbeat_engine.claimed == set()
