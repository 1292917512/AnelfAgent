"""主思维 Driver 的单航班、唤醒合并、任务认领与关停测试。"""

from __future__ import annotations

import asyncio
from types import MethodType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent.messages import build_entity_scope
from agent.mind.autonomous import MindTask, TaskType
from agent.mind.driver import MindDriver
from agent.mind.mind import Mind
from agent.mind.prefrontal_cortex import PrefrontalCortex


def _fake_mind() -> Any:
    claimed: set[str] = set()
    mind = SimpleNamespace(
        _reset_phase_if_idle=Mock(),
        _autonomous_cycle=AsyncMock(),
        _auto_cycle_retry=0,
        pfc=PrefrontalCortex(SimpleNamespace()),
    )
    mind._schedule_next_cycle = Mock(wraps=MethodType(Mind._schedule_next_cycle, mind))

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


@pytest.mark.parametrize("fails", [False, True])
async def test_tick_waiters_share_one_inflight_tick(fails: bool) -> None:
    mind = _fake_mind()
    entered = asyncio.Event()
    release = asyncio.Event()
    error = RuntimeError("tick failed")

    async def _tick() -> list[str]:
        entered.set()
        await release.wait()
        if fails:
            raise error
        return ["a"]

    mind.heartbeat_engine.tick = AsyncMock(side_effect=_tick)
    try:
        first = mind.driver.submit_tick()
        second = mind.driver.submit_tick()
        cancelled = mind.driver.submit_tick()
        cancelled.cancel()
        await asyncio.wait_for(entered.wait(), 1)
        late = mind.driver.submit_tick()
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(first, second, late, return_exceptions=True), 1,
        )
        assert results == [error if fails else ["a"]] * 3
        assert cancelled.cancelled()
        assert mind.heartbeat_engine.tick.await_count == 1
        # 完成后的请求开启下一次 tick，不复用上一次的终态。
        mind.heartbeat_engine.tick.side_effect = None
        mind.heartbeat_engine.tick.return_value = ["b"]
        assert await asyncio.wait_for(mind.driver.submit_tick(), 1) == ["b"]
        assert mind.heartbeat_engine.tick.await_count == 2
    finally:
        await mind.driver.close()


async def test_close_cancels_inflight_tick_waiters() -> None:
    mind = _fake_mind()
    entered = asyncio.Event()

    async def _tick() -> list[str]:
        entered.set()
        await asyncio.Event().wait()
        return []

    mind.heartbeat_engine.tick = AsyncMock(side_effect=_tick)
    try:
        first = mind.driver.submit_tick()
        await asyncio.wait_for(entered.wait(), 1)
        late = mind.driver.submit_tick()
        await mind.driver.close()
        assert first.cancelled() and late.cancelled()
        assert mind.driver.submit_tick().cancelled()
        assert mind.heartbeat_engine.tick.await_count == 1
    finally:
        await mind.driver.close()


@pytest.mark.parametrize("heartbeat", [False, True])
@pytest.mark.parametrize("pending", ["message", "general", "analysis"])
async def test_failed_cycle_recovers_pending_work(
    monkeypatch: pytest.MonkeyPatch, heartbeat: bool, pending: str,
) -> None:
    mind = _fake_mind()
    monkeypatch.setattr("core.config.get_config_float", lambda key, default: 0.1)
    scope = build_entity_scope("test", "user", "u1")
    if pending == "message":
        mind.pfc.pending_user.append(scope)
    elif pending == "general":
        mind.pfc.add_general_task(MindTask(task_type=TaskType.SELF_TASK, preview="backup"))
    else:
        mind.pfc.pending_analysis.append((0, "u1", "test"))
    recovered = asyncio.Event()

    async def _cycle(*, is_heartbeat: bool) -> None:
        if mind._autonomous_cycle.await_count == 1:
            raise RuntimeError("temporary failure before consuming work")
        if pending == "message":
            assert mind.pfc.pending_user.popleft() == scope
        elif pending == "general":
            assert mind.pfc.clear_general_tasks() == 1
        else:
            assert mind.pfc.pending_analysis.popleft() == (0, "u1", "test")
        recovered.set()

    mind._autonomous_cycle = AsyncMock(side_effect=_cycle)
    try:
        mind.driver.request_cycle(is_heartbeat=heartbeat)
        await asyncio.wait_for(mind.driver.when_idle(), 1)
        assert mind._autonomous_cycle.await_count == 1
        assert mind._schedule_next_cycle.call_args.kwargs == {"backoff": True}
        await asyncio.wait_for(recovered.wait(), 1)
        await asyncio.wait_for(mind.driver.when_idle(), 1)
        assert mind._autonomous_cycle.await_count == 2
        mind._autonomous_cycle.assert_awaited_with(is_heartbeat=False)
        assert not mind.pfc.has_pending_tasks() and not mind.pfc.pending_analysis
    finally:
        await mind.driver.close()


@pytest.mark.parametrize("cancelled", [False, True])
async def test_empty_failed_cycle_and_cancellation_do_not_retry(cancelled: bool) -> None:
    mind = _fake_mind()
    if cancelled:
        mind.pfc.add_general_task(MindTask(task_type=TaskType.SELF_TASK, preview="backup"))
    mind._autonomous_cycle.side_effect = asyncio.CancelledError() if cancelled else RuntimeError("failed")
    try:
        mind.driver.request_cycle()
        await asyncio.wait_for(mind.driver.when_idle(), 1)
        mind._schedule_next_cycle.assert_not_called()
    finally:
        await mind.driver.close()


def test_failure_backoff_is_bounded_even_with_waiting_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    mind = _fake_mind()
    mind.driver = SimpleNamespace(request_cycle=Mock())
    mind.pfc.pending_user.append(build_entity_scope("test", "user", "u1"))
    monkeypatch.setattr("core.config.get_config_float", lambda key, default: 0.0)
    for _ in range(9):
        mind._schedule_next_cycle("failure", backoff=True)
    assert [call.kwargs["delay"] for call in mind.driver.request_cycle.call_args_list] == pytest.approx(
        [0.1, 0.2, 0.4, 0.8, 1.6, 3.2, 6.4, 8.0, 8.0],
    )
    mind._auto_cycle_retry = 10000
    mind._schedule_next_cycle("failure", backoff=True)
    mind.driver.request_cycle.assert_called_with(delay=8.0)
    mind._schedule_next_cycle("normal continuation")
    mind.driver.request_cycle.assert_called_with(delay=0.0)


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
