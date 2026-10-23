"""界面提问的完成、取消与投递失败资源生命周期。"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from entities.ui import presence, tools


@pytest.fixture(autouse=True)
def online_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(presence, "_clients", {})
    tools.update_ui_state({
        "client_id": "test", "sequence": 1, "connected": True,
        "visible": True, "focused": True, "idle_seconds": 0,
    })


@pytest.mark.asyncio
async def test_ask_cancellation_releases_pending_future(monkeypatch: pytest.MonkeyPatch) -> None:
    emitted = asyncio.Event()

    async def emit(command: str, payload: dict) -> None:
        emitted.set()

    monkeypatch.setattr(tools, "_emit", emit)
    task = asyncio.create_task(tools.ui_ask("问题"))
    await emitted.wait()
    ask_id = next(iter(tools._pending_asks))
    future = tools._pending_asks[ask_id].future
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ask_id not in tools._pending_asks
    assert future.cancelled()


@pytest.mark.asyncio
async def test_ask_delivery_failure_does_not_leak_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "_emit", AsyncMock(side_effect=OSError("offline")))
    result = json.loads(await tools.ui_ask("问题"))
    assert result["error"]
    assert not tools._pending_asks


@pytest.mark.asyncio
async def test_ask_custom_answer_reaches_model(monkeypatch: pytest.MonkeyPatch) -> None:
    async def emit(command: str, payload: dict) -> None:
        if command == "ask":
            assert tools.resolve_ask(payload["ask_id"], "自定义回答")

    monkeypatch.setattr(tools, "_emit", emit)
    result = json.loads(await tools.ui_ask("问题", options=["选项"]))
    assert result == {"success": True, "answer": "自定义回答"}
    assert not tools._pending_asks


@pytest.mark.asyncio
@pytest.mark.parametrize("patch,status", [
    ({"closed": True}, "offline"), ({"connected": False}, "offline"),
    ({"visible": False}, "background"), ({"focused": False}, "background"),
    ({"idle_seconds": 301}, "idle"),
])
async def test_unattended_page_does_not_emit_or_wait(
    monkeypatch: pytest.MonkeyPatch, patch: dict, status: str,
) -> None:
    state = tools.get_ui_state_snapshot()
    tools.update_ui_state({**state, **patch, "sequence": 2})
    emit = AsyncMock()
    monkeypatch.setattr(tools, "_emit", emit)
    result = json.loads(await tools.ui_ask("问题"))
    assert result["cause"] == "state" and result["ui_status"] == status
    assert not tools._pending_asks
    emit.assert_not_awaited()


@pytest.mark.asyncio
async def test_presence_expires_and_other_tabs_cannot_hide_active_page(monkeypatch: pytest.MonkeyPatch) -> None:
    state = tools.get_ui_state_snapshot()
    tools.update_ui_state({**state, "client_id": "background", "visible": False})
    assert json.loads(await tools.ui_get_state())["can_ask"] is True
    tools.update_ui_state({**state, "sequence": 3, "closed": True})
    tools.update_ui_state({**state, "sequence": 2})
    assert json.loads(await tools.ui_get_state())["status"] == "background"
    now = presence.time.monotonic()
    monkeypatch.setattr(presence.time, "monotonic", lambda: now + 46)
    assert json.loads(await tools.ui_get_state()) == {
        "available": False, "can_ask": False, "status": "offline",
    }


@pytest.mark.asyncio
async def test_disconnect_ends_wait_and_broadcasts_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []

    async def emit(command: str, payload: dict) -> None:
        commands.append(command)

    async def disconnect(*args: object, **kwargs: object) -> None:
        tools.update_ui_state({**tools.get_ui_state_snapshot(), "sequence": 2, "closed": True})

    monkeypatch.setattr(tools, "_emit", emit)
    monkeypatch.setattr(tools.asyncio, "wait", disconnect)
    result = json.loads(await tools.ui_ask("问题"))
    assert result["ui_status"] == "offline"
    assert commands == ["ask", "ask_closed"]
    assert not tools._pending_asks


@pytest.mark.asyncio
async def test_skipped_question_is_not_reported_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def emit(command: str, payload: dict) -> None:
        tools.resolve_ask(payload["ask_id"], "__skipped__")

    monkeypatch.setattr(tools, "_emit", emit)
    result = json.loads(await tools.ui_ask("问题"))
    assert result["cause"] == "user_cancel" and result["retryable"] is False
    assert "success" not in result
