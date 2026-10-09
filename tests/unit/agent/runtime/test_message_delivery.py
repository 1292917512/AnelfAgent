import asyncio
from unittest.mock import AsyncMock

import pytest

from agent.runtime.agent_app import AgentApp, AgentEvent
from core.event_bus import EVENT_CHAT_BROADCAST, event_bus


def test_full_queue_rejects_new_input_without_losing_accepted_event() -> None:
    app = AgentApp()
    app._queue = asyncio.Queue(maxsize=1)
    first = AgentEvent("message", {"message_id": "first"})
    app._enqueue(first)
    with pytest.raises(RuntimeError, match="队列已满"):
        app._enqueue(AgentEvent("message", {"message_id": "second"}))
    assert app._queue.get_nowait() is first
    app._queue.task_done()


async def test_processing_failure_reports_own_message_and_chat() -> None:
    app = AgentApp()
    app.set_handler(AsyncMock(side_effect=RuntimeError("storage unavailable")))
    received: list[dict[str, object]] = []

    async def observe(payload: dict[str, object]) -> None:
        received.append(payload)

    event_bus.on(EVENT_CHAT_BROADCAST, observe, owner="test.delivery")
    try:
        await app.send_message(user_id="web_user", content="hello", adapter_key="webui", session_id="chat-a", message_id="message-a")
        await asyncio.wait_for(app._queue.join(), timeout=2)
        assert received == [{"event": "message_failed", "message_id": "message-a", "chat_id": "chat-a"}]
    finally:
        await app.stop()
        event_bus.off_by_owner("test.delivery")
