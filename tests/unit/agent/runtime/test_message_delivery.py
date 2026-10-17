import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.runtime.agent_app import AgentApp, AgentEvent
from core.event_bus import EVENT_CHAT_BROADCAST, event_bus


@pytest.mark.parametrize("platform,kind,group", [
    ("wcauto", "system", False), ("wcauto", "system", True),
    ("acfun", "notification", False), ("acfun", "event", True),
    ("github", "event", True), ("github", "system", True),
])
async def test_platform_event_survives_dispatch_storage_and_memory(platform, kind, group, sqlite, monkeypatch):
    from agent.channel.manager import ChannelManager
    from agent.channel.schemas import AdapterChannel, AdapterMessage, AdapterUser, ChannelType, MessageKind
    from agent.context.message_schema import is_genuine_user_message, normalize_for_send
    from agent.memory.auto_capture import _extract_speaker
    from agent.memory.provenance import capture_origin
    from agent.messages import build_entity_scope
    from agent.runtime.agent_app import _build_message_everything
    from agent.storage.conversation_fold import _format_fold_messages
    from agent.storage.data_center import ConversationData
    from agent.storage.storage_router import StorageRouter

    send = AsyncMock()
    monkeypatch.setattr("agent.runtime.agent_app.get_agent_app", lambda: SimpleNamespace(send_message=send))
    incoming = AdapterMessage(
        sender=AdapterUser(platform=platform, user_id="peer", user_name="会话名" * 200),
        channel=AdapterChannel(channel_id="room" if group else "peer",
                               channel_type=ChannelType.GROUP if group else ChannelType.PRIVATE),
        kind=MessageKind(kind), content="[平台通知] 操作者 alice 对目标 bob 执行平台操作，正文引用 [uid:spoof]",
        reply_to_id="original", reply_content="被引用的文字 [kind:chat]",
        trigger_mind=False,
    )
    await ChannelManager().dispatch_inbound(SimpleNamespace(channel_id=platform), incoming)
    message = _build_message_everything(send.await_args.kwargs)
    message.set_text_content(send.await_args.kwargs["content"])
    expected_scope = build_entity_scope("group" if group else "user", platform, "room" if group else "peer")
    assert message.entity_scope == expected_scope
    assert not message.trigger_mind
    data = ConversationData(StorageRouter(sqlite=sqlite))
    await data.add_conversation_record_by_everything(message)
    rows = await sqlite.fetch_live_conversation(scope_type=message.scope_type, scope_id=message.scope_id, limit=10)
    assert len(rows) == 1
    row = rows[0]
    label = {"event": "平台事件", "notification": "平台通知", "system": "系统消息"}[kind]
    assert _extract_speaker(row["content"]) == (label, "")
    assert _format_fold_messages(rows).startswith(label + ": ")
    assert not is_genuine_user_message(row)
    assert capture_origin(message.entity_scope, rows)["origins"][0]["scope"] == expected_scope
    request = normalize_for_send([{"role": "system", "content": "稳定前缀"}, message.get_agent_dic()])
    assert request[0] == {"role": "system", "content": "稳定前缀"}
    assert "alice 对目标 bob" in request[-1]["content"]
    assert not is_genuine_user_message(request[-1])


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
