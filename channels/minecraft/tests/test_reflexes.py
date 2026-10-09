"""本地生存桥的配置同步、事件归属与非阻塞播报回归。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from channels.minecraft.protocol import ConnectionStatus, SurvivalProgress, SurvivalStatus
from channels.minecraft.reflexes import SurvivalBridge


def status(runtime: str = "session", enabled: bool = True) -> SurvivalStatus:
    return SurvivalStatus(version=1, runtimeId=runtime, enabled=enabled, intervalMs=250)


def progress(phase: str = "started", runtime: str = "session", id_: str = "danger") -> dict[str, str]:
    return {"id": id_, "runtimeId": runtime, "kind": "drowning", "phase": phase}


def sync(bridge: SurvivalBridge, snapshot: SurvivalStatus | None) -> None:
    bridge.sync(snapshot, enabled=True, interval_seconds=0.25, scope="group_minecraft:local")


async def test_legacy_and_matching_status_do_not_poll_health_or_issue_actions() -> None:
    call = AsyncMock()
    bridge = SurvivalBridge(call, AsyncMock())
    sync(bridge, ConnectionStatus(status="online").survival)
    for _ in range(20):
        sync(bridge, status())
    await asyncio.sleep(0)
    call.assert_not_awaited()
    assert bridge._config_task is None


async def test_config_sync_singleflight_retries_and_reconnect() -> None:
    gate = asyncio.Event()
    now = [0.0]

    async def fail(name: str, args: dict) -> dict:
        assert name == "configure_survival" and args == {"enabled": True, "intervalMs": 250}
        await gate.wait()
        raise RuntimeError("temporarily unavailable")

    call = AsyncMock(side_effect=fail)
    bridge = SurvivalBridge(call, AsyncMock(), clock=lambda: now[0])
    try:
        sync(bridge, status(enabled=False))
        first = bridge._config_task
        for _ in range(20):
            sync(bridge, status(enabled=False))
        assert bridge._config_task is first
        gate.set()
        assert first is not None
        await first
        sync(bridge, status(enabled=False))
        assert call.await_count == 1
        now[0] = 6
        sync(bridge, status(enabled=False))
        assert bridge._config_task is not None
        await bridge._config_task
        assert call.await_count == 2
        sync(bridge, status("reconnected", enabled=False))
        await bridge._config_task
        assert call.await_count == 3
    finally:
        await bridge.stop()


async def test_events_are_validated_deduplicated_and_scoped_to_runtime() -> None:
    announce = AsyncMock()
    bridge = SurvivalBridge(AsyncMock(), announce)
    sync(bridge, status())
    try:
        bridge.event({"phase": "dead"})
        bridge.event(progress(runtime="old"))
        bridge.event(progress("moved"))
        bridge.event(progress())
        bridge.event(progress())
        await bridge._notices.join()
        assert announce.await_count == 1
        assert "头部入水" in announce.await_args.args[0]
        sync(bridge, status("reconnected"))
        bridge.event(progress(runtime="reconnected"))
        await bridge._notices.join()
        assert announce.await_count == 2
    finally:
        await bridge.stop()


async def test_slow_announcement_is_bounded_and_cancelled_without_blocking_events() -> None:
    entered = asyncio.Event()

    async def blocked(text: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    bridge = SurvivalBridge(AsyncMock(), blocked)
    sync(bridge, status())
    bridge.event(progress())
    await entered.wait()
    for index in range(100):
        bridge.event(progress(id_=str(index)))
    assert bridge._notices.qsize() == 32
    await asyncio.wait_for(bridge.stop(), 0.5)
    assert bridge._notice_task is None and bridge._notices.empty()


async def test_queued_facts_from_old_runtime_are_discarded() -> None:
    announce = AsyncMock()
    bridge = SurvivalBridge(AsyncMock(), announce)
    sync(bridge, status())
    bridge.event(progress())
    sync(bridge, status("new"))
    try:
        await bridge._notices.join()
        announce.assert_not_awaited()
    finally:
        await bridge.stop()


@pytest.mark.parametrize("phase", ["moved", "cancelled"])
def test_movement_end_does_not_claim_safety_or_completion(phase: str) -> None:
    assert SurvivalProgress.model_validate(progress(phase)).announcement() is None
