"""请求归属跨后台执行传播，停止只撤销对应执行器的旧请求。"""

import asyncio

import pytest

from core import tool_context as context


@pytest.fixture(autouse=True)
def controls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(context, "_controls", {})
    context.register_control("mc", "world", frozenset({"stop", "pause"}))


async def test_child_keeps_original_epoch_after_stop_and_new_request_is_current() -> None:
    gate = asyncio.Event()

    async def child() -> dict:
        await gate.wait()
        with context.tool_request("reflect", inherit=True):
            return context.control_metadata("mc", "dig", "worker")["anelf/action"]

    with context.tool_request("group_minecraft:world", "Alice") as request:
        task = asyncio.create_task(child())
    with context.tool_request("group_minecraft:world", "Bob"):
        stopped = context.control_metadata("mc", "stop")["anelf/action"]
    gate.set()
    old = await task
    assert old["requestId"] == request.request_id
    assert old["scope"] == "group_minecraft:world" and old["actor"] == "Alice"
    assert old["delegationId"] == "worker" and old["worldId"] == "world"
    assert old["epoch"] < old["floor"] == stopped["epoch"]
    assert not context.is_control_current("mc", old["producer"], old["epoch"])
    with context.tool_request():
        fresh = context.control_metadata("mc", "dig")["anelf/action"]
    assert context.is_control_current("mc", fresh["producer"], fresh["epoch"])
    assert context.control_metadata("unrelated", "stop") is None


def test_world_change_revokes_previous_request_and_reflex_stop_does_not() -> None:
    with context.tool_request(actor="@reflex"):
        before = context.control_metadata("mc", "stop")["anelf/action"]
        assert before["epoch"] == before["floor"] == 0
        context.register_control("mc", "other", frozenset({"stop"}))
        after = context.control_metadata("mc", "stop")["anelf/action"]
    assert after["epoch"] == 0 and after["floor"] == 1


def test_delegate_cleanup_is_scoped_and_bounded() -> None:
    context.register_control("other", "other-world", frozenset())
    with context.tool_request("user_webui:web_user#game"):
        context.control_metadata("mc", "get_state", "active")
        context.control_metadata("mc", "get_state", "done")
        context.control_metadata("other", "get_state", "other")
    context.forget_control_delegate("done")
    assert context.consume_control_delegates("mc") == {"active": "user_webui:web_user#game"}
    assert context.consume_control_delegates("mc") == {}
    assert "other" in context.consume_control_delegates("other")


def test_retry_retains_identity_but_refreshes_revocation_floor() -> None:
    before = context.control_metadata("mc", "dig")
    context.control_metadata("mc", "stop")
    retry = context.refresh_control_metadata("mc", before)["anelf/action"]
    assert retry["epoch"] == 0 and retry["floor"] == 1
    assert retry["requestId"] == before["anelf/action"]["requestId"]


async def test_trace_identity_is_inherited_without_changing_control_epochs() -> None:
    assert context.request_trace() == {}

    async def child() -> dict[str, str]:
        with context.tool_request("reflect", inherit=True, message_id="ignored"):
            return context.request_trace()

    with context.tool_request("group_minecraft:world", "Alice", message_id="mc-42") as origin:
        trace = await asyncio.create_task(child())
        assert trace == {"request_id": origin.request_id, "scope": origin.scope, "message_id": "mc-42"}
        trace["message_id"] = "changed-copy"
        assert context.request_trace()["message_id"] == "mc-42"
        assert context.control_metadata("mc", "dig")["anelf/action"]["epoch"] == 0
    assert context.request_trace() == {}
