"""自动回复、显式投递、结束边界与跨会话隔离。"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from helpers.think_loop_fakes import FakeMind, run_think_loop, text_result, tool_result

from agent.channel.delivery import delivery_tags
from agent.mind.tool_activation import bind_scope, reset_scope
from agent.mind.tools.reply_delivery import ReplyAssessment
from core.config import ConfigManager

_SCOPE = "user_qq:1"


@pytest.fixture
def sender(monkeypatch):
    async def send(channel_id, target_id, content, **kwargs):
        return json.dumps({"success": True, "delivery": delivery_tags("auto", "sent", target_id)})
    mock = AsyncMock(side_effect=send)
    monkeypatch.setattr("agent.mind.tools.reply_delivery.send_text_to_channel", mock)
    return mock


async def _run(rounds, anything, *, receipt=None, scope=_SCOPE, base=None):
    mind = FakeMind(rounds=rounds, default_text=None, tool_results={
        "send_message": json.dumps(receipt or {"success": True, "delivery": delivery_tags("channel", "sent", scope)}),
    })
    completion = {}
    token = bind_scope(scope)
    try:
        await run_think_loop(mind, anything=anything, completion=completion, base_messages=base)
    finally:
        reset_scope(token)
    return mind, completion


@pytest.mark.parametrize("scope,adapter,session", [
    (_SCOPE, "qq", ""), ("group_qq:1", "qq", ""), ("user_telegram:1", "telegram", ""),
    ("user_webui:web_user#chat_1", "webui", "chat_1"), ("user_feishu:ou_peer#oc_chat", "feishu", "oc_chat"),
])
async def test_plain_final_uses_current_conversation_once(anything, sender, scope, adapter, session):
    mind, completion = await _run([text_result("答案是 42。")], anything, scope=scope)
    sender.assert_awaited_once_with(adapter, scope, "答案是 42。", session_id=session, delivery_mode="auto")
    assert mind.llm_calls == 1
    assert completion["reason"] == "completed"
    assert "delivery_status:sent" in completion["messages"][-1]["content"]


@pytest.mark.parametrize("ending", [tool_result("内部收束说明", ["end_reply"]), text_result("end_reply()"), text_result("[SILENT]")])
async def test_explicit_end_never_sends_or_recovers(anything, sender, ending):
    mind, completion = await _run([ending], anything)
    assert mind.llm_calls == 1
    assert completion["reason"] == "completed"
    sender.assert_not_awaited()


async def test_tools_after_end_are_not_executed_and_results_stay_paired(anything, sender):
    mind, completion = await _run([tool_result("已结束", ["end_reply", "send_message", "recall"])], anything)
    assert mind.executed_tools == ["end_reply"]
    messages = completion["messages"]
    calls = [call["id"] for m in messages for call in m.get("tool_calls", [])]
    results = [m for m in messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in results] == calls
    assert all("未执行" in m["content"] for m in results[1:])
    sender.assert_not_awaited()


@pytest.mark.parametrize("interim,final", [(False, True), (True, True), (True, False), (False, False)])
async def test_interim_and_final_switches_are_independent(anything, sender, interim, final):
    ConfigManager.set("send_interim_text", interim)
    ConfigManager.set("send_final_text", final)
    mind, _ = await _run([tool_result("我正在查询", ["recall"]), text_result("查到的结果是 42。")], anything)
    assert mind.executed_tools == ["recall"]
    assert [call.args[2] for call in sender.await_args_list] == (
        (["我正在查询"] if interim else []) + (["查到的结果是 42。"] if final else []))


async def test_interim_is_sent_before_work_and_review_keeps_previous_evidence(anything, sender, monkeypatch):
    ConfigManager.set("send_interim_text", True)
    review = AsyncMock(return_value=ReplyAssessment(True, "通过"))
    monkeypatch.setattr("agent.mind.tools.reply_delivery.assess_reply", review)
    tool_started = asyncio.Event()

    class ObservedMind(FakeMind):
        @property
        def tool_executor(self):
            async def execute(tc):
                assert sender.await_count == 1
                tool_started.set()
                return '{"ok": true}'
            return execute

    mind = ObservedMind(rounds=[tool_result("马上为你检查。", ["recall"]), tool_result("", ["end_reply"])])
    token = bind_scope(_SCOPE)
    try:
        await run_think_loop(mind, anything=anything, base_messages=[{"role": "user", "content": "帮我检查"}])
    finally:
        reset_scope(token)
    assert tool_started.is_set()
    assert review.await_args.args[2] == [{"role": "user", "content": "帮我检查"}]
    assert sender.await_args.args[2] == "马上为你检查。"


@pytest.mark.parametrize("completion_timing", ["waiting", "during_delivery"])
async def test_waiting_progress_is_delivered_before_join_and_final_after_completion(anything, sender, sqlite, monkeypatch, completion_timing):
    from types import SimpleNamespace

    from agent.channel import output_tools
    from agent.channel.channel_types import ChannelStatus
    from agent.mind.background_tasks import BackgroundTaskRegistry
    from agent.storage.data_center import ConversationData
    from agent.storage.storage_router import StorageDomain, StorageRouter

    ConfigManager.set("send_interim_text", True)
    mind = FakeMind(rounds=[text_result("任务已启动，正在等结果。"), text_result("任务结果是 42。")], default_text=None)
    data = ConversationData(StorageRouter(sqlite=sqlite))
    mind.conversation_data = data
    anything.scope_type, anything.scope_id = "user", "qq:1"
    channel = SimpleNamespace(status=ChannelStatus.RUNNING, send_text=AsyncMock(return_value={"ok": True}),
                              conversation_scope_for_target=lambda tid, ct: None)
    monkeypatch.setattr(output_tools, "_get_channel", lambda cid: channel if cid == "qq" else None)
    monkeypatch.setattr(output_tools, "_speak_if_on_call", AsyncMock(return_value=""))
    monkeypatch.setattr(output_tools, "conversation_data_port", SimpleNamespace(bound=True, get=lambda: data))
    monkeypatch.setattr("agent.channel.outbound_guard._recent", {})
    sender.side_effect = output_tools.send_text_to_channel
    mind.background_tasks = BackgroundTaskRegistry()
    task_id = mind.background_tasks.register(_SCOPE, "delegation", "计算")
    notified = asyncio.Event()

    async def notify_completion(scope, description, summary, success):
        await data.router.append(StorageDomain.CONVERSATION, scope_type="user", scope_id="qq:1",
                                 role="system", content="[后台任务完成] " + summary)
        notified.set()
    mind.background_tasks.set_unclaimed_callback(notify_completion)

    async def complete():
        if completion_timing == "during_delivery":
            return
        while not mind.background_tasks._waiting.get(_SCOPE):
            await asyncio.sleep(0.005)
        assert sender.await_args.args[2] == "任务已启动，正在等结果。"
        mind.background_tasks.complete(task_id, True, "42")

    if completion_timing == "during_delivery":
        async def send_and_complete(*args, **kwargs):
            receipt = await output_tools.send_text_to_channel(*args, **kwargs)
            mind.background_tasks.complete(task_id, True, "42")
            await notified.wait()
            return receipt
        sender.side_effect = send_and_complete

    token = bind_scope(_SCOPE)
    finisher = asyncio.create_task(complete())
    try:
        await asyncio.wait_for(run_think_loop(mind, anything=anything), 3)
        await asyncio.wait_for(finisher, 1)
    finally:
        if not finisher.done():
            finisher.cancel()
        await asyncio.gather(finisher, return_exceptions=True)
        reset_scope(token)
    assert [call.args[2] for call in sender.await_args_list] == ["任务已启动，正在等结果。", "任务结果是 42。"]
    assert any("42" in m.get("content", "") for m in mind.sent_messages[1])
    assert channel.send_text.await_count == 2
    rows = await sqlite.fetch_live_conversation(scope_type="user", scope_id="qq:1", limit=10)
    assert [row["content"] for row in rows if row["role"] == "assistant"] == ["任务已启动，正在等结果。", "任务结果是 42。"]


@pytest.mark.parametrize("mode", ["reply", "reflect"])
async def test_end_output_and_internal_boundaries_with_interim_enabled(anything, sender, mode):
    from agent.mind.tools.think_loop import ThinkMode

    ConfigManager.set("send_interim_text", True)
    mind = FakeMind(rounds=[tool_result("仅内部备注", ["send_message", "end_reply"])], default_text=None)
    token = bind_scope(_SCOPE)
    try:
        await run_think_loop(mind, anything=anything, mode=ThinkMode(mode))
    finally:
        reset_scope(token)
    sender.assert_not_awaited()


async def test_closing_text_after_explicit_reply_is_not_sent_twice(anything, sender):
    mind, completion = await _run([tool_result("", ["send_message"]), text_result("好了我已回复。")], anything)
    assert mind.executed_tools == ["send_message"]
    assert "delivery_status:skipped" in completion["messages"][-1]["content"]
    sender.assert_not_awaited()


async def test_new_work_after_progress_allows_final(anything, sender):
    await _run([tool_result("", ["send_message"]), tool_result("查询中", ["recall"]), text_result("最终结果。")], anything)
    assert sender.await_args.args[2] == "最终结果。"


@pytest.mark.parametrize("target", ["user_telegram:1", "group_qq:1", "user_qq:2", "user_qq:1#other"])
async def test_other_target_receipt_does_not_block_current_reply(anything, sender, target):
    await _run([tool_result("", ["send_message"]), text_result("这是给你的结果。")], anything,
               receipt={"success": True, "delivery": delivery_tags("channel", "sent", target)})
    assert sender.await_args.args[1] == _SCOPE


async def test_unknown_send_is_not_retried_after_unrelated_work(anything, sender):
    ConfigManager.set("send_interim_text", True)
    await _run([tool_result("", ["send_message"]), tool_result("进度正文", ["recall"]), text_result("结果相同。")], anything,
               receipt={"success": False, "outcome": "unknown", "delivery": delivery_tags("channel", "unknown", _SCOPE)})
    sender.assert_not_awaited()


async def test_internal_event_has_no_automatic_delivery(anything, sender):
    await _run([text_result("技能核验已完成。")], anything, base=[
        {"role": "user", "content": "旧问题", "_source": {"origin": "conversation"}},
        {"role": "system", "content": "后台技能核验", "_source": {"origin": "conversation"}},
    ])
    sender.assert_not_awaited()


async def test_rejected_reply_leaves_context_fact_and_never_falls_back(anything, sender, monkeypatch):
    review = AsyncMock(return_value=ReplyAssessment(False, "Jev 不通过", "local"))
    monkeypatch.setattr("agent.mind.tools.reply_delivery.assess_reply", review)
    mind, completion = await _run([text_result("内部状态。")], anything)
    sender.assert_not_awaited()
    assert mind.llm_calls == 1
    assert "Jev 不通过" in completion["messages"][-1]["content"]
    assert "delivery_status:skipped" in str(mind._add_system_context.await_args)


async def test_rejected_interim_continues_work_and_explicit_send_without_review(anything, sender, monkeypatch):
    ConfigManager.set("send_interim_text", True)
    ConfigManager.set("send_final_text", False)
    review = AsyncMock(return_value=ReplyAssessment(False, "Jev 超时，不自动投递", "local"))
    monkeypatch.setattr("agent.mind.tools.reply_delivery.assess_reply", review)
    mind, completion = await _run([
        tool_result("我正在查询", ["recall"]),
        tool_result("", ["send_message", "end_reply"]),
    ], anything)
    assert mind.executed_tools == ["recall", "send_message", "end_reply"]
    review.assert_awaited_once()
    sender.assert_not_awaited()
    assert "Jev 超时" in str(mind.sent_messages[1])
    assert "delivery_status:sent" in str(completion["messages"])


async def test_failed_auto_send_is_not_reported_as_sent(anything, sender):
    sender.side_effect = None
    sender.return_value = json.dumps({"success": False, "error": "offline"})
    _, completion = await _run([text_result("答案。")], anything)
    assert completion["reason"] == "failed"
    assert "delivery_status:failed" in completion["messages"][-1]["content"]
    assert sender.await_count == 1


async def test_metadata_and_inline_reasoning_never_escape(anything, sender):
    await _run([text_result("<think>内部推理</think>" + delivery_tags("channel", "sent", "user_qq:2") + "答复")], anything)
    assert sender.await_args.args[2] == "答复"


@pytest.mark.parametrize("interim", [False, True])
async def test_interrupt_during_review_cancels_without_sending(anything, sender, monkeypatch, interim):
    ConfigManager.set("send_interim_text", True)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    async def review(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr("agent.mind.tools.reply_delivery.assess_reply", review)
    mind = FakeMind(rounds=[tool_result("检查中。", ["recall"]) if interim else text_result("答案。")], default_text=None)
    from agent.mind.interrupt import InterruptRegistry
    mind.interrupts = InterruptRegistry()
    token = bind_scope(_SCOPE)
    try:
        task = asyncio.create_task(run_think_loop(mind, anything=anything))
        await asyncio.wait_for(started.wait(), 2)
        mind.interrupts.request(_SCOPE, "用户停止")
        await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        reset_scope(token)
    assert cancelled.is_set()
    assert not mind.executed_tools
    sender.assert_not_awaited()


async def test_progress_and_work_in_same_batch_can_produce_final(anything, sender):
    await _run([tool_result("", ["send_message", "recall"]), text_result("最终结果。")], anything)
    assert sender.await_args.args[2] == "最终结果。"


async def test_interrupt_during_send_retains_unknown_receipt(anything, sender):
    from agent.mind.interrupt import InterruptRegistry
    started = asyncio.Event()
    async def sending(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()
    sender.side_effect = sending
    mind = FakeMind(rounds=[text_result("答案。")], default_text=None)
    mind.interrupts = InterruptRegistry()
    completion = {}
    token = bind_scope(_SCOPE)
    try:
        task = asyncio.create_task(run_think_loop(mind, anything=anything, completion=completion))
        await asyncio.wait_for(started.wait(), 2)
        mind.interrupts.request(_SCOPE, "停止")
        await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        reset_scope(token)
    assert completion["reason"] == "interrupted"
    assert any("delivery_status:unknown" in m.get("content", "") for m in completion["messages"])
    assert "可能已经送达" in str(mind._add_system_context.await_args)
    assert sender.await_count == 1
