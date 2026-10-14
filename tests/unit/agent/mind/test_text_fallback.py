"""工具过程、结束信号与频道事件交接不被自动回复替代。"""

from __future__ import annotations

import json
from typing import List

import pytest
from helpers.think_loop_fakes import FakeMind, run_think_loop, text_result, tool_result

_SEND_MESSAGE_RESULT = ('{"success": true, "target_id": "1", "message_id": "m1", '
                        '"delivery": "[delivery_mode:channel][delivery_status:sent][delivery_scope:_global]"}')


@pytest.mark.parametrize("accepted", [True, False])
async def test_event_handoff_ends_only_after_real_acceptance(anything, deliver_mock, monkeypatch, accepted) -> None:
    from channels.minecraft.reply_policy import companion_policy

    monkeypatch.setattr("agent.channel.reply_policy.get_reply_policy", lambda *a: companion_policy("minecraft"))
    monkeypatch.setattr("channels.minecraft.receipts.request_trace", lambda: {"request_id": "r", "scope": "s"})
    payload = {"id": "task", "actionId": "action", "active": True, "phase": "running",
               "origin": {"requestId": "r", "scope": "s"}} if accepted else {"error": "missing materials"}
    mind = FakeMind(tool_results={"prepare_item": json.dumps(payload), "send_message": _SEND_MESSAGE_RESULT})
    mind._rounds = [tool_result("internal only", ["prepare_item"]), tool_result("explanation", ["send_message"]),
                    tool_result("", ["end_reply"])]
    await _run(mind, anything)
    if accepted:
        assert mind.llm_calls == 1
        deliver_mock.assert_not_awaited()
    else:
        assert mind.llm_calls == 3
        deliver_mock.assert_not_awaited()

def _mind(text: str = "我先说两句～") -> FakeMind:
    return FakeMind(default_text=text, tool_results={"send_message": _SEND_MESSAGE_RESULT})

def _run(mind, anything, steps=None, chain=None, tools=None):
    return run_think_loop(mind, anything=anything, steps=steps, chain=chain, tools=tools)

async def test_bare_text_no_continue_or_sent_ack(anything, deliver_mock) -> None:
    """终态后不再注入「未调工具」催促或「已发送」假 assistant。"""
    mind = _mind()
    chain: List = []
    await _run(mind, anything, chain=chain)

    assert not any(
        "未调用工具" in m.get("content", "")
        for m in chain if m.get("role") == "system"
    )
    assert not any(
        "已发送给用户" in m.get("content", "")
        for m in chain if m.get("role") == "assistant"
    )

async def test_tool_round_reuses_output_contract_without_extra_hint(anything, deliver_mock) -> None:
    """输出契约留在执行上下文，文本伴随工具调用不再追加重复提示。"""
    mind = _mind()
    mind._rounds = [
        tool_result("我先查一下相关记忆", ["recall"]),
        tool_result("", ["end_reply"]),
    ]
    chain: List = []
    await _run(mind, anything, chain=chain)

    hints = [
        m for m in chain if m.get("role") == "system"
        and "仅你可见" in m.get("content", "")
    ]
    assert not hints
    for messages in mind.sent_messages:
        assert sum("工具结果与未投递正文仅内部可见" in m.get("content", "") for m in messages) == 1

async def test_silent_tool_round_no_visibility_hint(anything, deliver_mock) -> None:
    """静默工具轮（无伴随文本）不注入提示——exec_context 每轮已有输出契约。"""
    mind = _mind()
    mind._rounds = [
        tool_result("", ["recall"]),
        tool_result("", ["end_reply"]),
    ]
    chain: List = []
    await _run(mind, anything, chain=chain)

    hints = [
        m for m in chain if m.get("role") == "system"
        and "仅你可见" in m.get("content", "")
    ]
    assert not hints

async def test_send_message_no_sent_ack(anything, deliver_mock) -> None:
    """send_message 成功后不再注入「已发送」假 assistant。"""
    mind = _mind()
    mind._rounds = [
        tool_result("你好", ["send_message"]),
        tool_result("", ["end_reply"]),
    ]
    chain: List = []
    await _run(mind, anything, chain=chain)

    assert not any(
        "已发送给用户" in m.get("content", "")
        for m in chain if m.get("role") == "assistant"
    )
    assert not any(
        "仅你可见" in m.get("content", "")
        for m in chain if m.get("role") == "system"
    )

async def test_silent_marker_ends_turn(anything, deliver_mock) -> None:
    """[SILENT] 精确匹配：不投递，直接结束。"""
    mind = _mind(text="[SILENT]")
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 1
    deliver_mock.assert_not_awaited()
    assert any("沉默" in s for s in steps)

@pytest.mark.parametrize("narration", ["*沉默*", "（沉默）", "🔇", "…", "*(silent)*"])
async def test_silence_narration_ends_turn(anything, deliver_mock, narration) -> None:
    """幻觉沉默旁白：不投递，直接结束。"""
    mind = _mind(text=narration)
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 1
    deliver_mock.assert_not_awaited()

async def test_empty_output_quietly_ends(anything, deliver_mock) -> None:
    """空输出可接受，不注入纠正提示，连续 2 次安静结束。"""
    mind = _mind(text="")
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    deliver_mock.assert_not_awaited()
    assert not any("禁止" in m.get("content", "") for m in chain if m.get("role") == "system")

async def test_end_reply_content_not_delivered(anything, deliver_mock) -> None:
    """end_reply 同批带有 assistant 正文 → 静默收束，正文不投递。"""
    mind = _mind()
    mind._rounds = [tool_result("这是最后一段话～", ["end_reply"])]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_not_awaited()
    assert any("静默收束" in s for s in steps)

async def test_end_reply_content_suppressed_after_send_message(anything, deliver_mock) -> None:
    """同轮 send_message 已送达，end_reply 附带正文不再重复投递。"""
    mind = _mind()
    mind._rounds = [tool_result("补充一句", ["send_message", "end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()

async def test_text_form_end_reply_ends_without_delivery(anything, deliver_mock) -> None:
    """弱模型把 end_reply 写成文本：按结束意图处理，内部指令文本不投递。"""
    mind = _mind()
    mind._rounds = [text_result("end_reply(reason=群员闲聊与我无关，静默结束)")]
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    deliver_mock.assert_not_awaited()
    assert mind.llm_calls == 1
    assert any("按结束处理" in s for s in steps)
    # 规范入链：幻觉文本不留痕，轨迹里是 assistant tool_calls + tool 结果
    assert not any(
        m.get("role") == "assistant" and "end_reply(" in (m.get("content") or "")
        for m in chain
    )
    assistant_calls = [
        tc["function"]["name"] for m in chain if m.get("role") == "assistant"
        for tc in m.get("tool_calls") or []
    ]
    assert assistant_calls == ["end_reply"]
    assert any(
        m.get("role") == "tool" and '"action": "end_reply"' in m.get("content", "")
        for m in chain
    )
