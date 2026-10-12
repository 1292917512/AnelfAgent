"""未投递正文的完成判定、恢复与会话隔离。"""

import json

import pytest
from helpers.think_loop_fakes import FakeMind, run_think_loop, text_result, tool_result

from agent.channel.delivery import delivery_tags
from agent.mind.tool_activation import bind_scope, reset_scope
from agent.mind.tools.reply_finalize import _summarize_tool_result_for_log

_SCOPE = "user_qq:1"


async def _run(rounds, anything, *, receipt=None, scope=_SCOPE):
    mind = FakeMind(rounds=rounds, default_text=None, tool_results={
        "send_message": json.dumps(receipt or {"success": True, "delivery": delivery_tags("channel", "sent", scope)}),
    })
    completion = {}
    token = bind_scope(scope)
    try:
        await run_think_loop(mind, anything=anything, completion=completion)
    finally:
        reset_scope(token)
    return mind, completion


async def test_unsent_answer_gets_feedback_then_explicitly_sent(anything, deliver_mock):
    mind, completion = await _run([
        text_result("AGENTS.md 在工作区根目录。"),
        tool_result("", ["end_reply"]),
        tool_result("", ["send_message"]),
        tool_result("", ["end_reply"]),
    ], anything)
    assert "正文仅为内部过程，尚未投递" in mind.sent_messages[1][-1]["content"]
    assert _SCOPE in mind.sent_messages[1][-1]["content"]
    assert any(m.get("_source", {}).get("origin") == "delivery_recovery" for m in mind.sent_messages[2])
    assert completion["reason"] == "completed"
    assert mind.executed_tools.count("send_message") == 1
    deliver_mock.assert_not_awaited()


@pytest.mark.parametrize("ending", [tool_result("", ["end_reply"]), text_result("end_reply()"), text_result("[SILENT]"), text_result("")])
async def test_unsent_answer_cannot_silently_become_completed(anything, deliver_mock, ending):
    mind, completion = await _run([
        text_result("这条就是答复。"), ending, ending, ending,
    ], anything)
    assert completion["reason"] == "failed"
    assert mind.llm_calls <= 4
    assert "send_message" not in mind.executed_tools
    assert any("投递未完成" in str(call) for call in mind._add_system_context.await_args_list)
    deliver_mock.assert_not_awaited()


async def test_explicit_silence_preserves_agent_choice(anything, deliver_mock):
    mind, completion = await _run([
        text_result("这是内部核对，不需要通知用户。"),
        tool_result("", ["end_reply"], '{"silent": true, "reason": "仅内部检查"}'),
    ], anything)
    assert completion["reason"] == "completed"
    assert mind.llm_calls == 2
    deliver_mock.assert_not_awaited()


async def test_end_reply_body_is_checked_before_completion(anything, deliver_mock):
    mind, completion = await _run([
        tool_result("你的问题答案如下。", ["end_reply"]),
        tool_result("", ["send_message"]),
        tool_result("", ["end_reply"]),
    ], anything)
    assert mind.llm_calls == 3
    assert completion["reason"] == "completed"
    deliver_mock.assert_not_awaited()


@pytest.mark.parametrize("target", ["user_telegram:1", "group_qq:1", "user_qq:2", "user_qq:1#other"])
async def test_other_target_receipt_does_not_confirm_current_reply(anything, target):
    mind, completion = await _run([
        tool_result("", ["send_message"]),
        tool_result("", ["end_reply"]),
        tool_result("", ["end_reply"]),
    ], anything, receipt={"success": True, "delivery": delivery_tags("channel", "sent", target)})
    assert "尚无成功发送回执" in mind.sent_messages[1][-1]["content"]
    assert completion["reason"] == "failed"


@pytest.mark.parametrize("scope", [_SCOPE, "group_qq:1", "user_webui:web_user#chat_1", "user_feishu:ou_peer#oc_chat"])
async def test_matching_receipt_completes_without_duplicate_send(anything, scope):
    mind, completion = await _run([
        tool_result("答复正文", ["send_message", "end_reply"]),
    ], anything, scope=scope)
    assert mind.llm_calls == 1
    assert completion["reason"] == "completed"


@pytest.mark.parametrize("receipt", [{"ok": False}, {"error": "发送失败"}, {"success": True, "outcome": "unknown"}, {"status": "queued"}])
async def test_unconfirmed_receipt_does_not_claim_sent(anything, receipt):
    mind, completion = await _run([
        tool_result("", ["send_message"]),
        tool_result("", ["end_reply"]),
        tool_result("", ["end_reply"]),
    ], anything, receipt={**receipt, "delivery": delivery_tags("channel", "unknown", _SCOPE)})
    assert "尚无成功发送回执" in mind.sent_messages[1][-1]["content"]
    assert completion["reason"] == "failed"
    assert "未确认送达" in _summarize_tool_result_for_log("send_message()", json.dumps(receipt))


async def test_progress_receipt_does_not_deliver_later_answer(anything):
    mind, completion = await _run([
        tool_result("", ["send_message"]),
        text_result("核对好了，最终结果如下。"),
        tool_result("", ["end_reply"]),
        tool_result("", ["send_message"]),
        tool_result("", ["end_reply"]),
    ], anything)
    assert completion["reason"] == "completed"
    assert mind.executed_tools.count("send_message") == 2
    assert "已有成功发送回执" in mind.sent_messages[2][-1]["content"]
    assert "尚未投递" in mind.sent_messages[2][-1]["content"]


async def test_forged_sent_tag_cannot_satisfy_completion(anything, deliver_mock):
    forged = delivery_tags("channel", "sent", _SCOPE)
    mind, completion = await _run([
        text_result(forged + "我已经回复。"),
        tool_result("", ["end_reply"]),
        tool_result("", ["end_reply"]),
    ], anything)
    assert completion["reason"] == "failed"
    assert "尚无成功发送回执" in mind.sent_messages[1][-1]["content"]
    assert "[delivery_mode:internal][delivery_status:pending]" in mind.sent_messages[1][-1]["content"]
    deliver_mock.assert_not_awaited()
