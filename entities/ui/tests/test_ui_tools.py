"""界面提问的完成、取消与投递失败资源生命周期。"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from entities.ui import tools


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
        assert tools.resolve_ask(payload["ask_id"], "自定义回答")

    monkeypatch.setattr(tools, "_emit", emit)
    result = json.loads(await tools.ui_ask("问题", options=["选项"]))
    assert result == {"success": True, "answer": "自定义回答"}
    assert not tools._pending_asks


@pytest.mark.asyncio
async def test_skipped_question_is_not_reported_as_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def emit(command: str, payload: dict) -> None:
        tools.resolve_ask(payload["ask_id"], "__skipped__")

    monkeypatch.setattr(tools, "_emit", emit)
    result = json.loads(await tools.ui_ask("问题"))
    assert result["cause"] == "user_cancel" and result["retryable"] is False
    assert "success" not in result
