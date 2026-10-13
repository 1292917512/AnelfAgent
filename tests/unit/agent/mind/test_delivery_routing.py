"""真实发送管道、临时会话存储与并发思维轮次的投递边界。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from helpers.think_loop_fakes import FakeMind, run_think_loop, text_result, tool_result

from agent.channel import output_tools
from agent.channel.channel_types import ChannelStatus
from agent.mind.tool_activation import bind_scope, reset_scope
from agent.mind.tools.reply_delivery import ReplyAssessment
from agent.mind.tools.think_loop import ThinkMode
from agent.storage.data_center import ConversationData
from agent.storage.storage_router import StorageDomain, StorageRouter
from core.config import ConfigManager


@pytest.fixture
def pipeline(sqlite, monkeypatch):
    data = ConversationData(StorageRouter(sqlite=sqlite))
    channels = {name: SimpleNamespace(
        status=ChannelStatus.RUNNING,
        send_text=AsyncMock(return_value={"ok": True}),
        conversation_scope_for_target=lambda target, kind: None,
    ) for name in ("qq", "webui")}
    monkeypatch.setattr(output_tools, "_get_channel", channels.get)
    monkeypatch.setattr(output_tools, "_speak_if_on_call", AsyncMock(return_value=""))
    monkeypatch.setattr(output_tools, "conversation_data_port", SimpleNamespace(bound=True, get=lambda: data))
    monkeypatch.setattr("agent.channel.outbound_guard._recent", {})
    monkeypatch.setattr("agent.channel.outbound_guard._reply_scopes_provider", lambda: frozenset())
    ConfigManager.set("outbound_guard_empty_enabled", False)
    ConfigManager.set("send_interim_text", True)
    return data, channels


async def test_concurrent_channels_keep_review_target_and_history(pipeline, sqlite, monkeypatch):
    data, channels = pipeline
    both_reviewing = asyncio.Event()
    entered = 0

    async def assess(content, scope, messages):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_reviewing.set()
        await both_reviewing.wait()
        assert content == scope + "的进度"
        return ReplyAssessment(True, "通过")

    monkeypatch.setattr("agent.mind.tools.reply_delivery.assess_reply", assess)

    async def run(scope_id):
        scope = "user_" + scope_id
        anything = SimpleNamespace(scope_type="user", scope_id=scope_id, uid=1, group_id=0,
                                   get_text_content=lambda: "检查进度")
        mind = FakeMind(rounds=[tool_result(scope + "的进度", ["recall"]), tool_result("", ["end_reply"])],
                        default_text=None)
        mind.conversation_data = data
        completion = {}
        token = bind_scope(scope)
        try:
            await run_think_loop(mind, anything=anything, completion=completion)
        finally:
            reset_scope(token)
        rows = await sqlite.fetch_live_conversation(scope_type="user", scope_id=scope_id, limit=10)
        assert [r["content"] for r in rows if r["role"] == "assistant"] == [scope + "的进度"]
        assert any("delivery_scope:" + scope in m.get("content", "") for m in completion["messages"])

    await asyncio.wait_for(asyncio.gather(run("qq:1"), run("webui:1#chat_1")), 3)
    assert all(ch.send_text.await_count == 1 for ch in channels.values())


@pytest.mark.parametrize("approved", [False, True])
async def test_only_confirmed_body_enters_persistent_conversation(pipeline, sqlite, anything, monkeypatch, approved):
    data, channels = pipeline
    monkeypatch.setattr("agent.mind.tools.reply_delivery.assess_reply",
                        AsyncMock(return_value=ReplyAssessment(approved, "已核验")))
    anything.scope_type, anything.scope_id = "user", "qq:1"
    mind = FakeMind(rounds=[text_result("候选正文，只在通过时送达")], default_text=None)
    mind.conversation_data = data

    async def add_context(target, content, role):
        await data.router.append(StorageDomain.CONVERSATION, scope_type=target.scope_type,
                                 scope_id=target.scope_id, role=role, content=content)

    mind._add_system_context = add_context
    token = bind_scope("user_qq:1")
    completion = {}
    try:
        await run_think_loop(mind, anything=anything, completion=completion)
    finally:
        reset_scope(token)
    rows = await sqlite.fetch_live_conversation(scope_type="user", scope_id="qq:1", limit=10)
    assert any("候选正文" in m.get("content", "") for m in completion["messages"])
    assert any("候选正文" in r["content"] for r in rows) is approved
    assert channels["qq"].send_text.await_count == int(approved)
    if not approved:
        assert any("delivery_status:skipped" in r["content"] for r in rows)


async def test_reflection_can_explicitly_send_but_never_delivers_its_draft(pipeline, sqlite, monkeypatch):
    data, channels = pipeline
    review = AsyncMock(side_effect=AssertionError("内部反思不触发自动正文审核"))
    monkeypatch.setattr("agent.mind.tools.reply_delivery.assess_reply", review)

    class SendingMind(FakeMind):
        @property
        def tool_executor(self):
            async def execute(call):
                if call.name == "send_message":
                    return await output_tools.send_message(**json.loads(call.arguments))
                return '{"ok": true}'
            return execute

    mind = SendingMind(rounds=[
        text_result("内部自查草稿"),
        tool_result("内部结束备注", ["send_message", "end_reply"], json.dumps({
            "channel_id": "qq", "target_id": "user_qq:1", "content": "已确认需要通知的结果",
        })),
    ], default_text=None)
    mind.conversation_data = data
    completion = {}
    token = bind_scope("reflect:checks")
    try:
        await run_think_loop(mind, mode=ThinkMode.REFLECT, completion=completion)
    finally:
        reset_scope(token)
    rows = await sqlite.fetch_live_conversation(scope_type="user", scope_id="qq:1", limit=10)
    assert [r["content"] for r in rows] == ["已确认需要通知的结果"]
    assert channels["qq"].send_text.await_count == 1
    assert "内部自查草稿" in str(completion["messages"])
    assert "没有默认投递会话" in str(mind.sent_messages[0])
    review.assert_not_awaited()
