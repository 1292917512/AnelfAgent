"""主思维 Driver 的单航班、唤醒合并、任务认领与关停测试。"""

from __future__ import annotations

import asyncio
from itertools import permutations
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


async def test_heartbeat_yields_at_completed_tick_before_starting_another_model_cycle() -> None:
    mind = _fake_mind()
    entered, release = asyncio.Event(), asyncio.Event()

    async def tick(**kwargs):
        entered.set()
        await release.wait()
        return ["completed maintenance"]

    mind.heartbeat_engine.tick.side_effect = tick
    try:
        mind.driver.request_cycle(is_heartbeat=True)
        await asyncio.wait_for(entered.wait(), timeout=1)
        mind.driver.request_cycle()
        release.set()
        await asyncio.wait_for(mind.driver.when_idle(), timeout=1)
        mind._autonomous_cycle.assert_awaited_once_with(is_heartbeat=False)
        mind.heartbeat_engine.tick.assert_awaited_once()
    finally:
        release.set()
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

    async def _tick(**kwargs) -> list[str]:
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

    async def _tick(**kwargs) -> list[str]:
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
    scope = build_entity_scope("user", "test", "u1")
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
    mind.pfc.pending_user.append(build_entity_scope("user", "test", "u1"))
    monkeypatch.setattr("core.config.get_config_float", lambda key, default: 0.0)
    for _ in range(9):
        mind._schedule_next_cycle("failure", backoff=True)
    assert [call.kwargs["delay"] for call in mind.driver.request_cycle.call_args_list] == pytest.approx(
        [0.1, 0.2, 0.4, 0.8, 1.6],
    )
    mind._auto_cycle_retry = 10000
    mind._schedule_next_cycle("failure", backoff=True)
    assert mind.driver.request_cycle.call_count == 5
    assert mind.pfc.has_pending_tasks()
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


async def test_maintenance_checkpoint_services_messages_serially_and_preserves_merged_tick():
    mind = _fake_mind()
    entered, release = asyncio.Event(), asyncio.Event()
    user_started, user_release = asyncio.Event(), asyncio.Event()
    events = []

    async def tick(*, checkpoint, should_yield):
        events.append("maintenance_committed")
        entered.set()
        await release.wait()
        assert should_yield()
        await checkpoint()
        events.append("maintenance_resumed")
        return ["scheduled"]

    async def cycle(*, is_heartbeat):
        assert not is_heartbeat
        assert mind.driver.current_work == "cycle"
        events.append("user_started")
        user_started.set()
        await user_release.wait()
        events.append("user_finished")

    mind.heartbeat_engine.tick.side_effect = tick
    mind._autonomous_cycle.side_effect = cycle
    first = mind.driver.submit_tick()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        mind.driver.request_cycle()
        release.set()
        await asyncio.wait_for(user_started.wait(), 2)
        second = mind.driver.submit_tick()
        assert events == ["maintenance_committed", "user_started"]
        user_release.set()
        assert await asyncio.wait_for(first, 2) == await asyncio.wait_for(second, 2) == ["scheduled"]
        await mind.driver.when_idle()
        assert events[-2:] == ["user_finished", "maintenance_resumed"]
        mind.heartbeat_engine.tick.assert_awaited_once()
        mind._autonomous_cycle.assert_awaited_once()
    finally:
        user_release.set()
        release.set()
        await mind.driver.close()


async def test_multichannel_bursts_during_maintenance_are_never_lost_or_parallelized():
    mind = _fake_mind()
    scopes = [build_entity_scope("user", "qq", "42"), build_entity_scope("user", "telegram", "42"),
              build_entity_scope("group", "feishu", "42"), build_entity_scope("user", "webui", "42", session_id="a")]
    handled = []
    active = 0

    async def cycle(*, is_heartbeat):
        nonlocal active
        assert not is_heartbeat and active == 0
        active += 1
        try:
            for queue in (mind.pfc.pending_user, mind.pfc.pending_group):
                while not queue.is_empty():
                    handled.append(queue.popleft())
            await asyncio.sleep(0)
        finally:
            active -= 1

    async def tick(*, checkpoint, should_yield):
        for scope in scopes:
            (mind.pfc.pending_group if scope.startswith("group_") else mind.pfc.pending_user).append(scope)
            for _ in range(4):
                mind.driver.request_cycle()
            assert should_yield()
            await checkpoint()
            assert active == 0
        return []

    mind._autonomous_cycle.side_effect = cycle
    mind.heartbeat_engine.tick.side_effect = tick
    try:
        mind.driver.request_cycle(is_heartbeat=True)
        await asyncio.wait_for(mind.driver.when_idle(), 3)
        assert handled == scopes and len(set(handled)) == 4
        assert mind._autonomous_cycle.await_count == 4  # 心跳末尾不再补一次元决策。
    finally:
        await mind.driver.close()


async def test_close_during_interleaved_reply_cancels_tick_without_resuming_writes():
    mind = _fake_mind()
    entered = asyncio.Event()
    resumed = False

    async def cycle(*, is_heartbeat):
        entered.set()
        await asyncio.Event().wait()

    async def tick(*, checkpoint, should_yield):
        nonlocal resumed
        mind.driver.request_cycle()
        await checkpoint()
        resumed = True
        return []

    mind._autonomous_cycle.side_effect = cycle
    mind.heartbeat_engine.tick.side_effect = tick
    waiter = mind.driver.submit_tick()
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(mind.driver.close(), 2)
    assert waiter.cancelled() and not resumed


@pytest.mark.parametrize("arrival_order", list(permutations(("message", "task", "tick"))))
@pytest.mark.parametrize("task_fails", [False, True])
async def test_task_in_flight_orders_mixed_arrivals_without_losing_claims(arrival_order, task_fails):
    mind = _fake_mind()
    entered, release = asyncio.Event(), asyncio.Event()
    events: list[str] = []
    active = 0
    scopes = [build_entity_scope("user", adapter, "shared", "window") for adapter in ("first", "second")]

    async def run(name, *, trigger):
        nonlocal active
        assert active == 0
        active += 1
        events.append(name)
        try:
            if name == "running":
                entered.set()
                await release.wait()
                if task_fails:
                    raise RuntimeError("running task failed")
            return name
        finally:
            active -= 1
            mind.heartbeat_engine.release_task_claim(name)

    async def cycle(*, is_heartbeat):
        nonlocal active
        assert active == 0
        active += 1
        try:
            events.append("heartbeat" if is_heartbeat else "message")
            if not is_heartbeat:
                assert [mind.pfc.pending_user.popleft() for _ in scopes] == scopes
        finally:
            active -= 1

    async def tick(**kwargs):
        assert active == 0
        events.append("tick")
        return ["maintenance"]

    mind.heartbeat_engine.run_claimed_task = run
    mind.heartbeat_engine.tick.side_effect = tick
    mind._autonomous_cycle.side_effect = cycle
    first = mind.driver.submit_task("running")[2]
    queued = waiter = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for kind in arrival_order:
            if kind == "message":
                for scope in scopes:
                    mind.pfc.pending_user.append(scope)
                for _ in range(5):
                    mind.driver.request_cycle()
            elif kind == "task":
                accepted, _, queued = mind.driver.submit_task("queued")
                assert accepted
                assert mind.driver.submit_task("running")[0] is False
                assert mind.driver.submit_task("queued")[0] is False
            else:
                waiter = mind.driver.submit_tick()
                abandoned = mind.driver.submit_tick()
                abandoned.cancel()
        mind.driver.request_cycle(is_heartbeat=True)
        assert events == ["running"] and mind.driver.current_work == "task:running"
        release.set()
        await asyncio.wait_for(mind.driver.when_idle(), 2)
        assert await first == (None if task_fails else "running")
        assert await queued == "queued" and await waiter == ["maintenance"]
        assert events == ["running", "message", "queued", "tick", "tick", "heartbeat"]
        assert not mind.heartbeat_engine.claimed and not mind.pfc.has_pending_tasks()
    finally:
        release.set()
        await mind.driver.close()


async def test_message_arriving_in_interleaved_reply_is_latched_for_next_boundary():
    mind = _fake_mind()
    scope = build_entity_scope("user", "arbitrary", "shared", "window")
    entered, release = asyncio.Event(), asyncio.Event()
    handled: list[str] = []
    stages: list[str] = []
    queued = None

    async def cycle(*, is_heartbeat):
        assert not is_heartbeat
        assert mind.pfc.pending_user.popleft() == scope
        handled.append("first" if len(handled) == 0 else "new arrival")
        if len(handled) == 1:
            entered.set()
            await release.wait()

    async def tick(*, checkpoint, should_yield):
        stages.append("committed first")
        mind.pfc.pending_user.append(scope)
        mind.driver.request_cycle()
        await checkpoint()
        stages.append("committed second")
        assert should_yield()
        await checkpoint()
        stages.append("finished")
        return stages.copy()

    async def run(name, *, trigger):
        assert stages[-1] == "finished"
        mind.heartbeat_engine.release_task_claim(name)
        return name

    mind.heartbeat_engine.tick.side_effect = tick
    mind.heartbeat_engine.run_claimed_task = run
    mind._autonomous_cycle.side_effect = cycle
    waiter = mind.driver.submit_tick()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        mind.pfc.pending_user.append(scope)
        for _ in range(10):
            mind.driver.request_cycle()
        queued = mind.driver.submit_task("after-maintenance")[2]
        cancelled = mind.driver.submit_tick()
        cancelled.cancel()
        release.set()
        assert await asyncio.wait_for(waiter, 2) == ["committed first", "committed second", "finished"]
        await asyncio.wait_for(mind.driver.when_idle(), 2)
        assert await queued == "after-maintenance"
        assert handled == ["first", "new arrival"]
        mind.heartbeat_engine.tick.assert_awaited_once()
        assert not mind.pfc.has_pending_tasks() and not mind.heartbeat_engine.claimed
    finally:
        release.set()
        await mind.driver.close()


async def test_shutdown_rejects_new_work_and_preserves_unconsumed_messages():
    mind = _fake_mind()
    entered = asyncio.Event()
    scope = build_entity_scope("group", "arbitrary", "room")

    async def run(name, *, trigger):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            mind.heartbeat_engine.release_task_claim(name)

    mind.heartbeat_engine.run_claimed_task = run
    running = mind.driver.submit_task("running")[2]
    await asyncio.wait_for(entered.wait(), 2)
    queued = mind.driver.submit_task("queued")[2]
    tick = mind.driver.submit_tick()
    mind.pfc.pending_group.append(scope)
    mind.driver.request_cycle()
    mind.driver.request_cycle(delay=1)
    await asyncio.wait_for(mind.driver.close(), 2)
    assert running.cancelled() and queued.cancelled() and tick.cancelled()
    assert list(mind.pfc.pending_group.queue) == [scope]
    assert mind.driver.submit_task("late")[0] is False
    assert mind.driver.submit_tick().cancelled()
    mind.driver.request_cycle()
    mind.driver._fire_delayed_cycle()
    await mind.driver.when_idle()
    assert mind._autonomous_cycle.await_count == 0 and not mind.heartbeat_engine.claimed
    assert not mind.driver.is_busy


async def test_failed_interleaved_reply_keeps_work_and_recovers_without_repeating_maintenance():
    mind = _fake_mind()
    scope = build_entity_scope("user", "arbitrary", "shared")
    mind.pfc.pending_user.append(scope)
    stages = []
    mind._schedule_next_cycle = Mock()

    async def cycle(*, is_heartbeat):
        assert not is_heartbeat
        if mind._autonomous_cycle.await_count == 1:
            raise RuntimeError("model unavailable before consuming message")
        assert mind.pfc.pending_user.popleft() == scope

    async def tick(*, checkpoint, should_yield):
        stages.append("before")
        mind.driver.request_cycle()
        await checkpoint()
        stages.append("after")
        return ["maintenance complete"]

    mind.heartbeat_engine.tick.side_effect = tick
    mind._autonomous_cycle.side_effect = cycle
    try:
        assert await asyncio.wait_for(mind.driver.submit_tick(), 2) == ["maintenance complete"]
        assert list(mind.pfc.pending_user.queue) == [scope]
        mind._schedule_next_cycle.assert_called_once_with("用户周期异常后仍有待办", backoff=True)
        mind.driver.request_cycle()
        await asyncio.wait_for(mind.driver.when_idle(), 2)
        assert stages == ["before", "after"] and mind._autonomous_cycle.await_count == 2
        mind.heartbeat_engine.tick.assert_awaited_once()
        assert not mind.pfc.has_pending_tasks()
    finally:
        await mind.driver.close()


async def test_cancelled_task_waiters_do_not_release_claims_or_duplicate_admitted_work():
    mind = _fake_mind()
    entered, release = asyncio.Event(), asyncio.Event()
    executed = []

    async def run(name, *, trigger):
        executed.append(name)
        try:
            if name == "running":
                entered.set()
                await release.wait()
            return name
        finally:
            mind.heartbeat_engine.release_task_claim(name)

    mind.heartbeat_engine.run_claimed_task = run
    first = mind.driver.submit_task("running")[2]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = mind.driver.submit_task("queued")[2]
        first.cancel()
        second.cancel()
        assert mind.driver.submit_task("running")[0] is False
        assert mind.driver.submit_task("queued")[0] is False
        release.set()
        await asyncio.wait_for(mind.driver.when_idle(), 2)
        assert executed == ["running", "queued"]
        assert not mind.heartbeat_engine.claimed
    finally:
        release.set()
        await mind.driver.close()
