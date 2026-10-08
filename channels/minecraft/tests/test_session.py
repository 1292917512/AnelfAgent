"""陪玩任务取消、后台动作停止与系统事件历史的回归。"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent.channel.schemas import AdapterChannel, ChannelType
from agent.delegation.delegation_manager import DelegationManager
from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.tools import scheduler
from channels.minecraft import session
from channels.minecraft.adapter import MinecraftChannel
from channels.minecraft.config import MinecraftConfig
from channels.minecraft.protocol import ConnectionStatus, GameEvent
from core.entity import EntityRegistry


async def test_stop_cancels_world_workers_and_reminders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scheduler, "_reminders_path", lambda: tmp_path / "reminders.json")
    monkeypatch.setattr(DelegationManager, "_install_progress_hook", lambda self: None)
    router = SimpleNamespace(append=AsyncMock())
    mind = SimpleNamespace(
        pfc=SimpleNamespace(
            known_scopes=lambda: {"group_minecraft:local", "user_minecraft:local/Alice", "group_minecraft:other"},
            conversation_data=SimpleNamespace(router=router),
            consume_scope_task=Mock(),
        ),
        interrupt=Mock(),
        background_tasks=BackgroundTaskRegistry(),
    )
    manager = DelegationManager(mind)
    mind.delegation_manager = manager
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: SimpleNamespace(mind=mind))
    scopes = ["group_minecraft:local", "user_minecraft:local/Alice", "user_webui:web_user#game", "group_minecraft:other"]
    release = asyncio.Event()
    actions: list[str] = []

    async def worker(scope: str) -> None:
        await release.wait()
        actions.append(scope)

    tasks = [asyncio.create_task(worker(scope)) for scope in scopes]
    for i, (scope, task) in enumerate(zip(scopes, tasks, strict=True)):
        manager._running[str(i)] = {"scope": scope, "task": task, "agent": "mc-worker" if i == 2 else ""}
    for scope in scopes:
        await scheduler.add_reminder("继续游戏", 9999999999, scope)
    try:
        await session.stop_companion_work("local")
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert actions == ["group_minecraft:other"]
        assert {r["scope"] for r in scheduler._load_reminders()} == {"group_minecraft:other"}
        assert {call.args[0] for call in mind.interrupt.call_args_list} == set(scopes[:3])
        assert {call.args[0] for call in mind.pfc.consume_scope_task.call_args_list} == set(scopes[:3])
        assert router.append.await_count == 3
        for call in router.append.await_args_list:
            history = call.kwargs
            assert history["role"] == "system" and history["trigger_mind"] is False
            assert "[push:minecraft_stop]" in history["content"]
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_stop_drains_local_actions_before_remote_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = MinecraftChannel()
    monkeypatch.setattr(channel, "get_config", lambda: MinecraftConfig(enabled=True))
    channel._connection = ConnectionStatus(status="online", username="AnelfBot")
    order: list[str] = []
    started = asyncio.Event()

    async def action() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            order.append("local_cancelled")

    async def call(name: str, args: dict[str, object]) -> dict[str, bool]:
        order.append(name)
        return {"ok": True}

    async def stop_work(world_id: str) -> None:
        order.append("workers_cancelled")

    monkeypatch.setattr("channels.minecraft.adapter.stop_companion_work", stop_work)
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "on_message", AsyncMock())
    channel._spawn_background(action())
    await started.wait()
    try:
        await channel._cmd_stop("Alice", AdapterChannel(channel_id="local", channel_type=ChannelType.GROUP))
        assert order == ["workers_cancelled", "local_cancelled", "cancel_task", "stop_pathfinding", "clear_control_states", "cancel_collect"]
        channel._sync_reflexes()
        assert channel._reflex_task is None
        assert not channel._background
        await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="chat", data={"username": "Alice", "message": "接着玩"}))
        assert not channel._actions_paused
    finally:
        await channel.stop()
        EntityRegistry.unregister(channel.get_entity_name())


async def test_reflex_fact_reaches_history_once_despite_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = MinecraftChannel()
    router = SimpleNamespace(append=AsyncMock())
    runtime = SimpleNamespace(mind=SimpleNamespace(pfc=SimpleNamespace(conversation_data=SimpleNamespace(router=router))))
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: runtime)
    monkeypatch.setattr(channel, "get_config", lambda: MinecraftConfig(enabled=True))
    channel._connection = ConnectionStatus(status="online", username="AnelfBot")
    monkeypatch.setattr(channel, "_send_text", AsyncMock())
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "on_message", inbound)
    try:
        await channel._announce_reflex("我先撤一下")
        await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="chat", data={"username": "AnelfBot", "message": "我先撤一下"}))
        router.append.assert_awaited_once()
        assert router.append.call_args.kwargs["trigger_mind"] is False
        inbound.assert_not_awaited()
    finally:
        EntityRegistry.unregister(channel.get_entity_name())
