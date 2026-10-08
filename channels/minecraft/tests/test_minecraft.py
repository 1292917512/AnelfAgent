"""游戏聊天的路由、增量事件与动作取消回归。"""

import asyncio
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

from agent.channel.channel_types import ChannelStatus
from agent.channel.schemas import AdapterChannel, ChannelType, SegmentType, SendRequest, SendSegment
from channels.minecraft.adapter import MinecraftChannel
from channels.minecraft.config import MinecraftConfig
from channels.minecraft.protocol import ConnectionStatus, GameEvent, split_chat
from core.entity import EntityRegistry


@pytest.fixture
def channel(monkeypatch: pytest.MonkeyPatch) -> Iterator[MinecraftChannel]:
    instance = MinecraftChannel()
    config = MinecraftConfig(enabled=True)
    monkeypatch.setattr(instance, "get_config", lambda: config)
    instance._connection = ConnectionStatus(status="online", username="AnelfBot", version="26.1")
    yield instance
    EntityRegistry.unregister(instance.get_entity_name())


def chat_event(seq: int, message: str, *, username: str = "Alice", kind: str = "chat") -> GameEvent:
    return GameEvent(seq=seq, ts=1000, type=kind, data={"username": username, "message": message})


@pytest.mark.parametrize("message,tool", [("!pause", "pause_action"), ("暂停一下", "pause_action"), ("!resume", "resume_action")])
async def test_pause_resume_bypass_model(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch, message: str, tool: str) -> None:
    call = AsyncMock(return_value={"ok": True, "supported": True, "active": False})
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "_send_text", AsyncMock())
    monkeypatch.setattr(channel, "on_message", inbound)
    monkeypatch.setattr("channels.minecraft.adapter.stop_companion_work", AsyncMock())
    await channel._dispatch_event(chat_event(1, message))
    assert any(args.args[0] == tool for args in call.await_args_list)
    assert not inbound.await_args.args[0].trigger_mind


async def test_action_events_cannot_change_executor_survival_settings(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    call = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="action_progress", data={"phase": "running"}))
    call.assert_not_awaited()


async def test_duplicate_mining_terminal_is_announced_once_per_action(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    announce = AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    data = {"id": "mine", "actionId": "action1", "phase": "paused", "steps": 1, "dug": 2,
            "item": "cobblestone", "gained": 2, "returned": False}
    for seq in (1, 2):
        await channel._dispatch_event(GameEvent(seq=seq, ts=1000, type="mine_progress", data=data))
    assert announce.await_count == 1
    await channel._dispatch_event(GameEvent(seq=3, ts=1000, type="mine_progress", data={**data, "actionId": "action2"}))
    assert announce.await_count == 2


@pytest.mark.parametrize("phase,available,clean,expected", [
    ("completed", 1, True, "制作任务已完成"),
    ("cancelled", 1, True, "制作任务已取消"),
    ("completed", 0, True, "制作任务受阻"),
    ("completed", 1, False, "尚未确认临时材料收尾"),
])
async def test_production_terminal_deduplicates_and_never_calls_existing_tool_new(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
    phase: str, available: int, clean: bool, expected: str,
) -> None:
    announce, inbound = AsyncMock(), AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    monkeypatch.setattr(channel, "on_message", inbound)
    data = {"id": "production", "actionId": "craft-action", "phase": phase,
            "item": "wooden_pickaxe", "created": 0, "reused": 1, "available": available,
            "required": 1, "inventoryClean": clean}
    for seq in (1, 2):
        await channel._dispatch_event(GameEvent(seq=seq, ts=1000, type="production_progress", data=data))
    announce.assert_awaited_once()
    text = announce.call_args.args[0]
    assert expected in text and "新合成 0" in text and "复用原有 1" in text
    if available == 0 or not clean:
        assert "已完成" not in text
    inbound.assert_not_awaited()


async def test_production_without_inventory_evidence_is_ignored(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    announce = AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="production_progress", data={
        "id": "production", "actionId": "action", "phase": "completed",
    }))
    announce.assert_not_awaited()


@pytest.mark.parametrize("gained,returned,clean,phase,expected", [
    (3, True, True, "completed", "制作任务已完成"),
    (2, True, True, "completed", "制作任务受阻"),
    (3, False, True, "completed", "制作任务受阻"),
    (3, True, False, "completed", "制作任务受阻"),
    (3, True, True, "blocked", "制作任务受阻"),
    (1, False, True, "cancelled", "制作任务已取消"),
])
async def test_preparation_requires_gathering_receipt_return_and_production(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
    gained: int, returned: bool, clean: bool, phase: str, expected: str,
) -> None:
    announce, inbound = AsyncMock(), AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    monkeypatch.setattr(channel, "on_message", inbound)
    data = {"id": "preparation", "actionId": "prepare-action", "phase": phase,
            "item": "wooden_pickaxe", "created": 1, "reused": 0, "available": 1,
            "required": 1, "inventoryClean": True,
            "gathering": {"item": "oak_log", "requested": 3, "gained": gained, "returned": returned,
                          "phase": "completed", "inventoryClean": clean}}
    for seq in (1, 2):
        await channel._dispatch_event(GameEvent(seq=seq, ts=1000, type="production_progress", data=data))
    announce.assert_awaited_once()
    assert expected in announce.call_args.args[0]
    assert f"前置采集新入包 {gained}/3" in announce.call_args.args[0]
    inbound.assert_not_awaited()


@pytest.mark.parametrize("phase,confirmed,clean,available,expected", [
    ("completed", True, True, 8, "补给任务已完成"),
    ("cancelled", True, True, 4, "补给任务已取消"),
    ("completed", False, True, 8, "双方库存尚未完整确认"),
    ("completed", True, False, 8, "临时物品收尾尚未确认"),
    ("completed", True, True, 4, "补给任务受阻"),
])
async def test_supply_terminal_requires_accounting_target_and_cleanup(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
    phase: str, confirmed: bool, clean: bool, available: int, expected: str,
) -> None:
    announce, inbound = AsyncMock(), AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    monkeypatch.setattr(channel, "on_message", inbound)
    data = {"id": "supply", "actionId": "supply-action", "phase": phase, "confirmed": confirmed,
            "inventoryClean": clean, "items": [{"item": "bread", "deposited": 0, "withdrawn": available,
                                                "available": available, "target": 8, "verified": confirmed}]}
    for seq in (1, 2):
        await channel._dispatch_event(GameEvent(seq=seq, ts=1000, type="supply_progress", data=data))
    announce.assert_awaited_once()
    text = announce.call_args.args[0]
    assert expected in text
    if not confirmed or not clean or available != 8:
        assert "已完成" not in text
    inbound.assert_not_awaited()


async def test_supply_without_evidence_is_ignored(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    announce = AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="supply_progress", data={
        "id": "supply", "actionId": "supply-action", "phase": "completed",
    }))
    announce.assert_not_awaited()


@pytest.mark.parametrize("phase,gained,returned,deposited,expected", [
    ("completed", 2, True, 2, "采集任务已完成"),
    ("completed", 0, True, 0, "采集任务受阻"),
    ("completed", 2, False, 2, "采集任务受阻"),
    ("completed", 2, True, 0, "采集任务受阻"),
    ("cancelled", 1, False, 0, "采集任务已取消"),
])
async def test_gather_requires_receipt_return_and_requested_delivery(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
    phase: str, gained: int, returned: bool, deposited: int, expected: str,
) -> None:
    announce, inbound = AsyncMock(), AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    monkeypatch.setattr(channel, "on_message", inbound)
    data = {"id": "gather", "actionId": "gather-action", "phase": phase, "dug": 2,
            "item": "cobblestone", "gained": gained, "requested": 2, "returned": returned,
            "depositRequested": True, "deposited": deposited, "inventoryClean": True}
    for seq in (1, 2):
        await channel._dispatch_event(GameEvent(seq=seq, ts=1000, type="gather_progress", data=data))
    announce.assert_awaited_once()
    assert expected in announce.call_args.args[0]
    assert f"新入包 {gained}/2" in announce.call_args.args[0]
    inbound.assert_not_awaited()


async def test_gather_without_evidence_is_ignored(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    announce = AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="gather_progress", data={
        "id": "gather", "actionId": "action", "phase": "completed",
    }))
    announce.assert_not_awaited()


def test_reply_policy_uses_configured_server_and_requires_delegation(channel: MinecraftChannel) -> None:
    channel.get_config().mcp_server = "game-test"
    policy = channel.reply_policy
    assert policy.direct_reply
    assert policy.tool_groups == ("mcp:game-test",)
    assert "mc-worker" in policy.instructions and "background=true" in policy.instructions
    assert "背包增量" in policy.instructions


@pytest.mark.parametrize("phase", ["running", "returning"])
async def test_mining_intermediate_events_do_not_chat(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    announce = AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="mine_progress", data={
        "phase": phase, "steps": 2, "dug": 3, "item": "cobblestone", "gained": 1, "returned": False,
    }))
    announce.assert_not_awaited()


async def test_mining_terminal_reports_inventory_and_return_facts(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    announce, inbound = AsyncMock(), AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="mine_progress", data={
        "phase": "blocked", "steps": 2, "dug": 3, "item": "cobblestone", "gained": 0, "returned": False,
    }))
    text = announce.call_args.args[0]
    assert "遇到障碍" in text
    assert "确认挖掉 3" in text and "背包净增加 0" in text
    assert "尚未确认回到入口" in text
    inbound.assert_not_awaited()


async def test_malformed_mining_event_is_ignored(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    announce = AsyncMock()
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="mine_progress", data={"phase": "completed"}))
    announce.assert_not_awaited()


async def test_slow_mining_announcement_does_not_delay_stop_dispatch(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()

    async def announce(text: str) -> None:
        entered.set()
        await asyncio.Event().wait()

    call = AsyncMock(return_value={"ok": True, "stopped": True})
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "_announce_reflex", announce)
    monkeypatch.setattr(channel, "_send_text", AsyncMock())
    monkeypatch.setattr(channel, "on_message", AsyncMock())
    monkeypatch.setattr("channels.minecraft.adapter.stop_companion_work", AsyncMock())
    try:
        await channel._dispatch_event(GameEvent(seq=1, ts=1000, type="mine_progress", data={
            "id": "mine", "phase": "blocked", "steps": 2, "dug": 3, "item": "cobblestone", "gained": 0, "returned": False,
        }), enqueue_commands=True)
        await entered.wait()
        await asyncio.wait_for(channel._dispatch_event(chat_event(2, "!stop"), enqueue_commands=True), 0.5)
        call.assert_awaited_once_with("cancel_task", {})
    finally:
        await channel._survival.stop()


async def test_public_mention_routes_as_group(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "@AnelfBot 带我去挖矿"))
    message = inbound.call_args.args[0]
    assert message.trigger_mind and message.is_to_me
    assert message.channel.channel_id == "local"
    assert message.channel.channel_type == ChannelType.GROUP
    assert message.sender.user_id == "local/Alice"


async def test_unaddressed_chat_is_history_only(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    channel.get_config().require_mention = True
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "我们去挖矿"))
    assert not inbound.call_args.args[0].trigger_mind


@pytest.mark.parametrize("text,addressed", [("你好@AnelfBot跟着我", True), ("@AnelfBot2 跟着我", False)])
async def test_mention_uses_player_name_boundaries(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    addressed: bool,
) -> None:
    channel.get_config().require_mention = True
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, text))
    assert inbound.call_args.args[0].trigger_mind is addressed


async def test_mention_not_required_wakes_on_plain_chat(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "你怎么又死了"))
    assert inbound.call_args.args[0].trigger_mind


async def test_whisper_and_bot_echo(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "你好", kind="whisper"))
    await channel._dispatch_event(chat_event(2, "回复", username="AnelfBot"))
    assert inbound.await_count == 1
    assert inbound.call_args.args[0].trigger_mind
    assert inbound.call_args.args[0].channel.channel_type == ChannelType.PRIVATE


async def test_player_filter_and_malformed_event(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    channel.get_config().allowed_players = ["Alice"]
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "@AnelfBot 你好", username="Bob"))
    await channel._dispatch_event(GameEvent(seq=2, ts=1000, type="chat", data={"username": "../bad"}))
    inbound.assert_not_awaited()


async def test_stop_cancels_before_reply(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    call = AsyncMock(return_value={"ok": True})
    reply = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", reply)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "@AnelfBot !stop"))
    assert [c.args[0] for c in call.call_args_list] == [
        "cancel_task",
        "stop_pathfinding",
        "clear_control_states",
        "cancel_collect",
    ]
    reply.assert_awaited_once()
    inbound.assert_awaited_once()
    assert inbound.call_args.args[0].content == "@AnelfBot !stop"
    assert inbound.call_args.args[0].trigger_mind is False


async def test_stop_failure_does_not_block_other_cancellations_or_inbound(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = AsyncMock(side_effect=[RuntimeError("cancel failed"), {}, {}, {}])
    reply = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", reply)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "@AnelfBot !stop"))
    assert call.await_count == 4
    inbound.assert_awaited_once()
    assert "未能确认" in reply.call_args.args[0].segments[0].content


async def test_event_cursor_skips_old_and_duplicate_messages(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = {"status": "online", "username": "AnelfBot"}
    first = {"events": [], "nextSince": 10}
    second = {"events": [chat_event(11, "@AnelfBot 你好").model_dump()], "nextSince": 11}
    call = AsyncMock(side_effect=[status, first, status, second, status, second])
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "on_message", inbound)
    monkeypatch.setattr(channel, "forward_message", AsyncMock())
    for _ in range(3):
        await channel._poll_once()
    inbound.assert_awaited_once()
    assert call.call_args_list[1].args[1]["types"] == ["__anelf_cursor__"]
    assert call.call_args_list[3].args[1]["since"] == 10
    assert channel._cursor == 11


async def test_executor_restart_resets_cursor(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    channel._cursor = 100
    call = AsyncMock(side_effect=[{"status": "disconnected"}, {"events": [], "nextSince": 0}])
    monkeypatch.setattr(channel, "_call", call)
    await channel._poll_once()
    assert channel._cursor == 0


async def test_reply_uses_private_target(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    call = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(channel, "_call", call)
    response = await channel.forward_message(
        SendRequest(
            adapter_key="minecraft",
            channel=AdapterChannel(channel_id="local/Alice"),
            segments=[SendSegment(type=SegmentType.TEXT, content="你好")],
        )
    )
    assert response.success
    call.assert_awaited_once_with("whisper", {"username": "Alice", "message": "你好"})


async def test_private_chunks_reserve_command_header(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(channel, "_call", call)
    username = "A" * 16
    result = await channel.forward_message(
        SendRequest(
            adapter_key="minecraft",
            channel=AdapterChannel(channel_id=f"local/{username}"),
            segments=[SendSegment(type=SegmentType.TEXT, content="😀" * 125)],
        )
    )
    assert result.success
    messages = [c.args[1]["message"] for c in call.call_args_list]
    assert "".join(messages) == "😀" * 125
    assert all(len(f"/tell {username} {m}".encode("utf-16-le")) // 2 <= 256 for m in messages)


@pytest.mark.parametrize(
    "target,text",
    [
        ("other/Alice", "你好"),
        ("local/Alice", "/op Alice"),
        ("local/Alice", "a" * 240 + "/op Alice"),
    ],
)
async def test_reply_rejects_wrong_world_and_commands(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    text: str,
) -> None:
    call = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    result = await channel.forward_message(
        SendRequest(
            adapter_key="minecraft",
            channel=AdapterChannel(channel_id=target),
            segments=[SendSegment(type=SegmentType.TEXT, content=text)],
        )
    )
    assert not result.success
    call.assert_not_awaited()


def test_chat_chunks_fit_java_limit() -> None:
    text = "😀" * 150 + "\n中文"
    chunks = split_chat(text)
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 240 for chunk in chunks)
    assert "".join(chunks) == text.replace("\n", "")


def test_chunk_boundary_cannot_become_a_command() -> None:
    with pytest.raises(ValueError, match="命令"):
        split_chat("a" * 240 + "/op Alice")


async def test_stop_closes_polling_task(channel: MinecraftChannel) -> None:
    await channel.start()
    task = channel._poll_task
    await channel.stop()
    assert task is not None and task.cancelled()


class TestLanDiscovery:
    def test_parse_announcement(self) -> None:
        from channels.minecraft.discovery import parse_announcement

        world = parse_announcement("[MOTD]My World[/MOTD][AD]61234[/AD]".encode(), "127.0.0.1", now=100.0)
        assert world is not None
        assert world.port == 61234 and world.motd == "My World" and world.host == "127.0.0.1"
        assert parse_announcement(b"garbage", "127.0.0.1") is None
        assert parse_announcement("[MOTD]x[/MOTD][AD]99999[/AD]".encode(), "127.0.0.1") is None
        assert parse_announcement(b"\x00[MOTD]x[/MOTD][AD]25565[/AD]\x00", "10.0.0.2") is not None

    def test_listen_rejects_short_wait(self) -> None:
        from channels.minecraft.discovery import listen_lan_announcements

        with pytest.raises(ValueError, match="至少"):
            listen_lan_announcements(0.1)

    async def test_discover_worlds_tool_shape(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from channels.minecraft import discovery as discovery_module
        from channels.minecraft.discovery import LanWorld

        monkeypatch.setattr(
            discovery_module,
            "listen_lan_announcements",
            lambda wait: [
                LanWorld(host="127.0.0.1", port=61234, motd="My World", last_seen=time.time() - 0.5),
            ],
        )
        result = await discovery_module.discover_worlds_tool(wait_seconds=2.0)
        assert result["success"] and len(result["worlds"]) == 1
        assert result["worlds"][0]["port"] == 61234
        assert result["worlds"][0]["seen_seconds_ago"] <= 2.0

    async def test_discover_worlds_empty_and_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from channels.minecraft import discovery as discovery_module

        monkeypatch.setattr(discovery_module, "listen_lan_announcements", lambda wait: [])
        empty = await discovery_module.discover_worlds_tool()
        assert empty["success"] and empty["worlds"] == [] and "对局域网开放" in empty["hint"]

        def _boom(wait: float) -> list:
            raise OSError("bind 失败")

        monkeypatch.setattr(discovery_module, "listen_lan_announcements", _boom)
        failed = await discovery_module.discover_worlds_tool()
        assert not failed["success"] and "bind 失败" in failed["error"]


class TestInstantAck:
    """收到回执已移除（与快捷指令面重复）：普通消息只入队，零出站调用。"""

    async def test_plain_message_sends_nothing_before_reply(
        self,
        channel: MinecraftChannel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        outbound = AsyncMock()
        inbound = AsyncMock()
        monkeypatch.setattr(channel, "forward_message", outbound)
        monkeypatch.setattr(channel, "on_message", inbound)
        await channel._dispatch_event(chat_event(1, "@AnelfBot 帮我采集木头"))
        outbound.assert_not_awaited()
        inbound.assert_awaited_once()

    async def test_stop_path_sends_only_confirm(
        self,
        channel: MinecraftChannel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        call = AsyncMock(return_value={"ok": True})
        outbound = AsyncMock(return_value=type("R", (), {"success": True})())
        inbound = AsyncMock()
        monkeypatch.setattr(channel, "_call", call)
        monkeypatch.setattr(channel, "forward_message", outbound)
        monkeypatch.setattr(channel, "on_message", inbound)
        await channel._dispatch_event(chat_event(1, "@AnelfBot !stop"))
        outbound.assert_awaited_once()  # 只有停止确认


async def _drain_background(channel: MinecraftChannel) -> None:
    for task in list(channel._background):
        await task


async def test_come_command_finds_player_and_goes_in_background(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    player = {"id": 123, "username": "Alice", "position": {"x": 10.5, "y": 64.0, "z": -3.5}}
    call = AsyncMock(side_effect=[player, {"ok": True}])
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "@AnelfBot !come"))
    # 立即回执“我来了！”，寻路在后台不占事件循环
    assert outbound.call_args_list[0].args[0].segments[0].content == "我来了！"
    find_args = call.call_args_list[0].args
    assert find_args[0] == "find_nearest_entity"
    assert find_args[1]["username"] == "Alice"
    await _drain_background(channel)
    goto_args = call.call_args_list[1].args
    assert goto_args[0] == "goto"
    assert goto_args[1]["goalType"] == "near"
    assert goto_args[1]["x"] == 10.5 and goto_args[1]["range"] == 2
    texts = [c.args[0].segments[0].content for c in outbound.call_args_list]
    assert "我到了！" in texts
    inbound.assert_awaited_once()


async def test_come_command_reports_when_player_not_visible(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = AsyncMock(return_value={"found": False})
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "@AnelfBot !过来"))
    call.assert_awaited_once()
    assert "看不到你在哪" in outbound.call_args.args[0].segments[0].content
    assert not channel._background
    inbound.assert_awaited_once()


async def test_follow_command_uses_entity_id(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    player = {"id": 77, "username": "Alice", "position": {"x": 0, "y": 64, "z": 0}}
    call = AsyncMock(return_value=player)
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "@AnelfBot !跟着我"))
    follow_args = call.call_args_list[1].args
    assert follow_args[0] == "follow_entity"
    assert follow_args[1] == {"entityId": 77, "range": 2}
    assert "跟着你了" in outbound.call_args.args[0].segments[0].content
    inbound.assert_awaited_once()


async def test_unknown_bang_message_falls_through_to_ai(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = AsyncMock()
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "@AnelfBot !dance"))
    call.assert_not_awaited()
    inbound.assert_awaited_once()
    outbound.assert_not_awaited()  # 未识别指令无任何出站，走正常 AI 路径


async def test_natural_phrase_come_triggers_directly(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    player = {"id": 5, "username": "Alice", "position": {"x": 1.0, "y": 64.0, "z": 2.0}}
    call = AsyncMock(side_effect=[player, {"ok": True}])
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "过来！"))
    assert call.call_args_list[0].args[0] == "find_nearest_entity"
    assert outbound.call_args_list[0].args[0].segments[0].content == "我来了！"
    await _drain_background(channel)
    inbound.assert_awaited_once()


async def test_natural_phrase_follow_and_stop(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    player = {"id": 9, "username": "Alice", "position": {"x": 0, "y": 64, "z": 0}}
    call = AsyncMock(return_value=player)
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "跟着我"))
    assert call.call_args_list[1].args[0] == "follow_entity"
    await channel._dispatch_event(chat_event(2, "别动"))
    stop_names = [c.args[0] for c in call.call_args_list[2:6]]
    assert stop_names == ["cancel_task", "stop_pathfinding", "clear_control_states", "cancel_collect"]


@pytest.mark.parametrize("text", ["别过来", "过来帮我看看这个", "我先不过来", "跟着我看那边"])
async def test_near_miss_phrases_fall_through_to_ai(
    channel: MinecraftChannel,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
) -> None:
    call = AsyncMock()
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, text))
    call.assert_not_awaited()
    inbound.assert_awaited_once()


async def test_command_message_is_history_only(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    player = {"id": 3, "username": "Alice", "position": {"x": 0, "y": 64, "z": 0}}
    call = AsyncMock(side_effect=[player, {"ok": True}])
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "过来"))
    msg = inbound.call_args.args[0]
    assert msg.is_to_me and msg.trigger_mind is False
    await _drain_background(channel)


async def test_reflexes_start_with_channel_and_stop_cleanly(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = AsyncMock(side_effect=[{"status": "disconnected"}])
    monkeypatch.setattr(channel, "_call", call)

    await channel.start()
    assert channel._survival._notice_task is not None and not channel._survival._notice_task.done()

    await channel.stop()
    assert channel._survival._notice_task is None
    assert channel._poll_task is None


async def test_reflexes_disabled_by_config(channel: MinecraftChannel) -> None:
    channel.get_config().reflexes_enabled = False

    await channel.start()

    assert channel._survival._config_task is None
    await channel.stop()


async def test_shutdown_cancels_workers_before_graceful_disconnect(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def stop_work(world_id: str, *, server: str, reason: str) -> None:
        assert world_id == "local" and "频道关闭" in reason
        order.append("workers")

    async def call(tool_name: str, args: dict[str, object]) -> dict[str, object]:
        assert tool_name == "disconnect_bot"
        assert "force" not in args
        assert channel._poll_task is None and channel._survival._notice_task is None
        order.append("disconnect")
        return {"status": "disconnected", "inventory": {"restored": True}}

    channel._status = ChannelStatus.RUNNING
    monkeypatch.setattr("channels.minecraft.adapter.stop_companion_work", stop_work)
    monkeypatch.setattr(channel, "_call", call)
    await channel.stop()
    await channel.stop()
    assert order == ["workers", "disconnect"]
    assert channel._status == ChannelStatus.STOPPED


async def test_shutdown_reports_incomplete_inventory_recovery(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import Mock

    channel._status = ChannelStatus.RUNNING
    monkeypatch.setattr("channels.minecraft.adapter.stop_companion_work", AsyncMock())
    monkeypatch.setattr(channel, "_call", AsyncMock(return_value={"inventory": {"restored": False, "reason": "Inventory full"}}))
    logger = Mock()
    monkeypatch.setattr("channels.minecraft.adapter.log", logger)
    await channel.stop()
    assert any("Inventory full" in call.args[0] for call in logger.call_args_list)
    assert channel._status == ChannelStatus.STOPPED


async def test_shutdown_still_disconnects_if_worker_cancellation_fails(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel._status = ChannelStatus.RUNNING
    monkeypatch.setattr("channels.minecraft.adapter.stop_companion_work", AsyncMock(side_effect=RuntimeError("worker busy")))
    call = AsyncMock(side_effect=RuntimeError("executor unavailable"))
    monkeypatch.setattr(channel, "_call", call)
    await channel.stop()
    call.assert_awaited_once()
    assert channel._status == ChannelStatus.STOPPED


async def test_reflexes_sync_to_config_toggle(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = {"version": 1, "runtimeId": "session1", "enabled": False, "intervalMs": 250}
    calls: list[str] = []

    async def invoke(name: str, args: dict) -> dict:
        calls.append(name)
        if name == "get_connection_status":
            return {"status": "online", "survival": dict(settings)}
        if name == "configure_survival":
            settings.update(args)
            return dict(settings)
        assert name == "get_events"
        return {"events": [], "nextSince": 1}

    call = AsyncMock(side_effect=invoke)
    monkeypatch.setattr(channel, "_call", call)
    try:
        await channel._poll_once()
        assert channel._survival._config_task is not None
        await channel._survival._config_task
        await channel._poll_once()
        assert calls == ["get_connection_status", "get_events", "configure_survival", "get_connection_status", "get_events"]
        channel.get_config().reflexes_enabled = False
        channel._survival._retry_at = 0
        await channel._poll_once()
        await channel._survival._config_task
        assert settings["enabled"] is False
    finally:
        await channel._survival.stop()


async def test_reflex_announce_targets_world_channel(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    outbound = AsyncMock()
    monkeypatch.setattr(channel, "forward_message", outbound)

    await channel._announce_reflex("疼疼疼——我先撤一下！")

    request = outbound.call_args.args[0]
    assert request.channel.channel_id == "local"
    assert request.channel.channel_type == ChannelType.GROUP


async def test_stay_cancels_work_and_disables_idle_actions(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = AsyncMock(return_value={"ok": True, "stopped": True})
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "待着"))

    assert [c.args[0] for c in call.await_args_list] == ["cancel_task"]
    text = outbound.call_args.args[0].segments[0].content
    assert "待着" in text
    assert inbound.call_args.args[0].trigger_mind is False


async def test_stop_pending_cleanup_is_not_reported_as_stopped(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(channel, "_call", AsyncMock(return_value={"ok": True, "stopped": False}))
    monkeypatch.setattr("channels.minecraft.adapter.stop_companion_work", AsyncMock())
    result = await channel._cmd_stop("Alice", AdapterChannel(channel_id="local", channel_type=ChannelType.GROUP))
    assert "未能确认停止" in result


async def test_polling_stop_overtakes_a_command_waiting_for_player_lookup(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, stopped = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def call(name: str, args: dict) -> dict:
        calls.append(name)
        if name == "find_nearest_entity":
            entered.set()
            await asyncio.Future()
        if name == "cancel_task":
            stopped.set()
            return {"ok": True, "stopped": True}
        return {}

    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "_send_text", AsyncMock())
    monkeypatch.setattr(channel, "on_message", AsyncMock())
    monkeypatch.setattr("channels.minecraft.adapter.stop_companion_work", AsyncMock())
    await channel._dispatch_event(chat_event(1, "!follow"), enqueue_commands=True)
    await asyncio.wait_for(entered.wait(), timeout=1)
    await asyncio.wait_for(channel._dispatch_event(chat_event(2, "!stop"), enqueue_commands=True), timeout=1)
    assert stopped.is_set()
    assert "follow_entity" not in calls
    assert not channel._commands


async def test_stop_reaches_executor_before_slow_bookkeeping(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch,
) -> None:
    physical, interrupted, finish_history = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def call(name: str, args: dict) -> dict:
        assert name == "cancel_task"
        assert interrupted.is_set(), "workers must not submit another action after the stop"
        physical.set()
        return {"ok": True, "stopped": True}

    async def bookkeeping(world_id: str, *, server: str) -> None:
        interrupted.set()
        await finish_history.wait()

    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr("channels.minecraft.adapter.stop_companion_work", bookkeeping)
    stopping = asyncio.create_task(channel._cmd_stop("Alice", AdapterChannel(channel_id="local", channel_type=ChannelType.GROUP)))
    try:
        await asyncio.wait_for(physical.wait(), timeout=1)
        assert not stopping.done(), "slow history must not delay physical stop"
    finally:
        finish_history.set()
        await stopping


async def test_sethome_then_home_goes_to_saved_position(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = AsyncMock(side_effect=[{"position": {"x": 10.0, "y": 64.0, "z": -3.0}}, {"ok": True}])
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "!sethome"))
    assert channel._home == {"x": 10.0, "y": 64.0, "z": -3.0}

    outbound.reset_mock()
    call.side_effect = [{"ok": True}]
    await channel._dispatch_event(chat_event(2, "!home"))
    await _drain_background(channel)
    goto = [c for c in call.await_args_list if c.args[0] == "goto"][0]
    assert goto.args[1]["x"] == 10.0 and goto.args[1]["z"] == -3.0


async def test_home_without_sethome_tells_player(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = AsyncMock()
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "回家"))

    call.assert_not_awaited()
    assert "还没设家" in outbound.call_args.args[0].segments[0].content


async def test_give_command_tosses_matched_item(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    player = {"id": 3, "position": {"x": 0, "y": 64, "z": 0}}
    inventory = {"items": [{"name": "oak_log", "count": 12}, {"name": "torch", "count": 5}]}
    call = AsyncMock(side_effect=[player, inventory, {"ok": True}])
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "!give oak_log 10"))

    toss = [c for c in call.await_args_list if c.args[0] == "toss_item"][0]
    assert toss.args[1] == {"item": "oak_log", "count": 10}
    assert "10 个" in outbound.call_args.args[0].segments[0].content


async def test_give_clamps_to_available_count(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    player = {"id": 3, "position": {"x": 0, "y": 64, "z": 0}}
    inventory = {"items": [{"name": "oak_log", "count": 3}]}
    call = AsyncMock(side_effect=[player, inventory, {"ok": True}])
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "!give oak_log 99"))

    toss = [c for c in call.await_args_list if c.args[0] == "toss_item"][0]
    assert toss.args[1]["count"] == 3


async def test_give_unknown_item_reports_missing(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    player = {"id": 3, "position": {"x": 0, "y": 64, "z": 0}}
    inventory = {"items": [{"name": "dirt", "count": 64}]}
    call = AsyncMock(side_effect=[player, inventory])
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "!give diamond 1"))

    assert not any(c.args[0] == "toss_item" for c in call.await_args_list)
    assert "没有" in outbound.call_args.args[0].segments[0].content


async def test_give_requires_nearby_player(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    call = AsyncMock(return_value={"found": False})
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "!give oak_log 1"))

    assert "太远" in outbound.call_args.args[0].segments[0].content


async def test_mark_then_go_uses_saved_position(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    position = {"position": {"x": 55.0, "y": 40.0, "z": -12.0}}
    call = AsyncMock(side_effect=[position, {"ok": True}])
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "!mark 矿洞口"))
    assert "矿洞口" in channel._marks
    reply = outbound.call_args.args[0].segments[0].content
    assert "(55, 40, -12)" in reply

    outbound.reset_mock()
    call.side_effect = [{"ok": True}]
    await channel._dispatch_event(chat_event(2, "!去 矿洞口"))
    immediate = outbound.call_args.args[0].segments[0].content
    assert "矿洞口" in immediate  # 先回执，后台寻路
    await _drain_background(channel)
    goto = [c for c in call.await_args_list if c.args[0] == "goto"][0]
    assert goto.args[1]["x"] == 55.0 and goto.args[1]["z"] == -12.0


async def test_go_unknown_name_lists_known_marks(
    channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel._marks["pit"] = {"name": "矿洞口", "pos": {"x": 1, "y": 64, "z": 1}}
    call = AsyncMock()
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "!去 温泉"))

    call.assert_not_awaited()
    text = outbound.call_args.args[0].segments[0].content
    assert "矿洞口" in text


async def test_mark_rejects_bad_names(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    call = AsyncMock()
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "!mark 有 空格"))
    await channel._dispatch_event(chat_event(2, "!mark "))

    call.assert_not_awaited()
    assert channel._marks == {}
    assert "地名" in outbound.call_args.args[0].segments[0].content


async def test_marks_lists_saved_locations(channel: MinecraftChannel, monkeypatch: pytest.MonkeyPatch) -> None:
    channel._marks = {
        "pit": {"name": "矿洞口", "pos": {"x": 1, "y": 64, "z": 1}},
        "lake": {"name": "湖边", "pos": {"x": 9, "y": 63, "z": -4}},
    }
    call = AsyncMock()
    outbound = AsyncMock()
    inbound = AsyncMock()
    monkeypatch.setattr(channel, "_call", call)
    monkeypatch.setattr(channel, "forward_message", outbound)
    monkeypatch.setattr(channel, "on_message", inbound)
    await channel._dispatch_event(chat_event(1, "!marks"))

    text = outbound.call_args.args[0].segments[0].content
    assert "矿洞口" in text and "湖边" in text
