"""过程正文不投递；回复仅由输出工具完成，结束、停滞与异常保持静默。"""

from __future__ import annotations

import json
from typing import List

import pytest
from helpers.think_loop_fakes import FakeMind, run_think_loop, text_result, tool_result

from agent.messages.everything import EverythingGroup

_SEND_MESSAGE_RESULT = '{"success": true, "target_id": "1", "message_id": "m1"}'


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


# ==================================================================
# 纯文本独白与回复隔离
# ==================================================================

async def test_bare_text_dropped_at_end_reply(anything, deliver_mock) -> None:
    """独白暂存后调 end_reply：静默收束，暂存独白不投递。"""
    mind = _mind()
    mind._rounds = [
        text_result("我先说两句～"),
        tool_result("", ["end_reply"]),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_not_awaited()
    assert mind.llm_calls == 2
    assert any("静默收束" in s for s in steps)


async def test_bare_text_monologue_cutoff_never_delivers(anything, deliver_mock) -> None:
    """模型持续独白到达上限时仍不能将过程正文外发。"""
    mind = _mind()
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_not_awaited()
    assert mind.llm_calls == 5  # text_without_tool_limit
    assert any("掐断结束" in s for s in steps)


async def test_group_monologue_is_not_a_reply(deliver_mock) -> None:
    """群消息与私聊使用同一输出契约，独白不得自动投递。"""
    anything = EverythingGroup(adapter_key="qq", uid=42, group_id=777, text_content="hi")
    mind = _mind(text="群里见～")
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    deliver_mock.assert_not_awaited()
    assert not any("路由询问" in m.get("content", "") for m in chain if m.get("role") == "system")
    assert mind.llm_calls == 5  # text_without_tool_limit


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


async def test_non_output_tools_inject_visibility_hint(anything, deliver_mock) -> None:
    """查资料类工具伴随文本独白时注入「结果仅你可见」（独白 = 误以为文字可达用户）。"""
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
    assert hints


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


async def test_tool_then_bare_text_dropped_at_end_reply(anything, deliver_mock) -> None:
    """非输出工具后输出最终纯文本再 end_reply：静默收束，独白不投递。"""
    mind = _mind()
    mind._rounds = [
        tool_result("", ["recall"]),
        text_result("查到了，结果是这样～"),
        tool_result("", ["end_reply"]),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_not_awaited()
    assert mind.llm_calls == 3


async def test_send_message_then_bare_text_not_delivered(anything, deliver_mock) -> None:
    """send_message 成功后继续独白：end_reply 静默收束，收尾独白不重复投递。"""
    mind = _mind()
    mind._rounds = [
        tool_result("", ["send_message"]),
        text_result("补充说明一下～"),
        tool_result("", ["end_reply"]),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_not_awaited()
    assert any("静默收束" in s for s in steps)


async def test_send_message_then_other_tool_then_text_not_delivered(
        anything, deliver_mock,
) -> None:
    """send_message 后再调其他工具，随后独白掐断也不再投递（送达标记跨轮持续）。"""
    mind = _mind()
    mind._rounds = [
        tool_result("", ["send_message"]),
        tool_result("", ["recall"]),
        *[text_result("补充最终结论～")] * 5,
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_not_awaited()
    assert mind.llm_calls == 7  # 2 轮工具 + 连续 5 轮独白掐断
    assert any("掐断结束" in s for s in steps)


async def test_send_message_mixed_with_other_tool_then_text_not_delivered(
        anything, deliver_mock,
) -> None:
    """同轮 send_message+recall 已送达，其后纯文本不再投递。"""
    mind = _mind()
    mind._rounds = [
        tool_result("", ["send_message", "recall"]),
        text_result("混合轮后的最终答复～"),
        tool_result("", ["end_reply"]),
    ]
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()


async def test_bare_text_no_thought_label(anything, deliver_mock) -> None:
    """纯文本不应以 '[思维]' 标签入库。"""
    mind = _mind()
    await _run(mind, anything)

    thought_labels = [
        c for c in mind._add_system_context.await_args_list
        if "[思维]" in (c.kwargs.get("content") or (c.args[1] if len(c.args) > 1 else ""))
    ]
    assert not thought_labels


# ==================================================================
# 多会话默认路由
# ==================================================================

async def test_multi_pending_never_broadcasts_monologue(anything, deliver_mock) -> None:
    """其他会话待处理时，独白也不能误发到任何频道。"""
    mind = _mind(text="大家好！")
    mind.pfc.pending_tasks = [("group_777", "0", "777", "群消息预览")]
    mind.pfc.adapter_keys = {"group_777": "qq"}
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 5  # text_without_tool_limit
    assert not any("路由询问" in m.get("content", "") for m in chain if m.get("role") == "system")
    deliver_mock.assert_not_awaited()


# ==================================================================
# 沉默/伪造/空输出
# ==================================================================

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


async def test_silence_word_in_sentence_does_not_end_early(anything, deliver_mock) -> None:
    """正文中提到 [SILENT] 不误判为结束指令，也不会自动投递。"""
    mind = _mind(text="我不太想用 [SILENT] 这种方式回应你")
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()
    assert mind.llm_calls == 5  # text_without_tool_limit


async def test_empty_output_quietly_ends(anything, deliver_mock) -> None:
    """空输出可接受，不注入纠正提示，连续 2 次安静结束。"""
    mind = _mind(text="")
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    deliver_mock.assert_not_awaited()
    assert not any("禁止" in m.get("content", "") for m in chain if m.get("role") == "system")


async def test_fake_tool_call_not_delivered(anything, deliver_mock) -> None:
    """伪造工具调用文本：与普通正文一致，仅保留在过程区。"""
    mind = _mind(text='[工具执行记录] send_message {"success": true}')
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()


# ==================================================================
# end_reply 静默收束
# ==================================================================

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


async def test_end_reply_empty_content_not_delivered(anything, deliver_mock) -> None:
    """end_reply 无正文 → 不投递。"""
    mind = _mind()
    mind._rounds = [tool_result("", ["end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()


# ==================================================================
# 文本形态工具调用（弱模型把 function calling 写成文本）
# ==================================================================

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


async def test_text_form_end_reply_drops_earlier_pending(anything, deliver_mock) -> None:
    """先有正常独白、再输出文本形态 end_reply：按静默收束处理，暂存独白不投递。"""
    mind = _mind()
    mind._rounds = [
        text_result("这是给你的答复～"),
        text_result("end_reply()"),
    ]
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()


async def test_tool_call_shaped_text_filtered_at_delivery(anything, deliver_mock) -> None:
    """整条是字面工具调用形态的独白：不能冒充真实发送调用。"""
    mind = _mind(text='send_message(content="你好")')
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()
