"""纯文本独白轮末保底投递 + 多频道路由（think_loop）单元测试。

输出契约：回复一律走 send_message；纯文本在循环内只是独白（不终局、不中途
投递），轮结束（end_reply / 沉默 / 空输出 / 独白上限掐断）时经轮末统一投递点
保底送达来源会话一次。跨会话发送走 switch_session / send_message 工具。
"""

from __future__ import annotations

from typing import List

import pytest
from helpers.think_loop_fakes import FakeMind, run_think_loop, text_result, tool_result

from agent.channel.reply_route import looks_like_tool_call_text
from agent.messages.everything import EverythingGroup

_SEND_MESSAGE_RESULT = '{"success": true, "target_id": "1", "message_id": "m1"}'


def _mind(text: str = "我先说两句～") -> FakeMind:
    return FakeMind(default_text=text, tool_results={"send_message": _SEND_MESSAGE_RESULT})


def _run(mind, anything, steps=None, chain=None, tools=None):
    return run_think_loop(mind, anything=anything, steps=steps, chain=chain, tools=tools)


# ==================================================================
# 纯文本独白轮末保底投递
# ==================================================================

async def test_bare_text_delivered_at_round_end(anything, deliver_mock) -> None:
    """纯文本独白不中途投递；end_reply 结束后轮末保底投递到来源会话。"""
    mind = _mind()
    mind._rounds = [
        text_result("我先说两句～"),
        tool_result("", ["end_reply"]),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_awaited_once()
    target, content = deliver_mock.await_args.args
    assert target.session_key == "test:private:1"
    assert content == "我先说两句～"
    assert mind.llm_calls == 2
    assert any("未送达文本已投递" in s for s in steps)


async def test_bare_text_monologue_cutoff_still_delivers(anything, deliver_mock) -> None:
    """模型一直独白不调工具：连续上限掐断，最后一段独白仍保底投递。"""
    mind = _mind()
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "我先说两句～"
    assert mind.llm_calls == 5  # text_without_tool_limit
    assert any("掐断结束" in s for s in steps)


async def test_same_group_direct_reply(deliver_mock) -> None:
    """同源群消息：独白轮末直接回到该群，不问 AI。"""
    anything = EverythingGroup(adapter_key="qq", uid=42, group_id=777, text_content="hi")
    mind = _mind()
    mind._rounds = [
        text_result("群里见～"),
        tool_result("", ["end_reply"]),
    ]
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    deliver_mock.assert_awaited_once()
    target, content = deliver_mock.await_args.args
    assert target.session_key == "qq:group:777"
    assert content == "群里见～"
    assert not any("路由询问" in m.get("content", "") for m in chain if m.get("role") == "system")
    assert mind.llm_calls == 2


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
    """查资料类工具后注入「结果仅你可见」。"""
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
    assert hints


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


async def test_tool_then_bare_text_delivered_at_end(anything, deliver_mock) -> None:
    """非输出工具后输出最终纯文本：独白暂存，轮末投递一次。"""
    mind = _mind()
    mind._rounds = [
        tool_result("", ["recall"]),
        text_result("查到了，结果是这样～"),
        tool_result("", ["end_reply"]),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_awaited_once()
    assert mind.llm_calls == 3
    assert any("未送达文本已投递" in s for s in steps)


async def test_send_message_then_bare_text_still_delivered(anything, deliver_mock) -> None:
    """send_message 成功后继续独白：轮末仍保底投递（不区分是否已走正路）。"""
    mind = _mind()
    mind._rounds = [
        tool_result("", ["send_message"]),
        text_result("补充说明一下～"),
        tool_result("", ["end_reply"]),
    ]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "补充说明一下～"


async def test_send_message_then_other_tool_then_text_delivers(
        anything, deliver_mock,
) -> None:
    """send_message 后再调其他工具，随后纯文本仍可投递。"""
    mind = _mind()
    mind._rounds = [
        tool_result("", ["send_message"]),
        tool_result("", ["recall"]),
        text_result("补充最终结论～"),
        tool_result("", ["end_reply"]),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "补充最终结论～"
    assert mind.llm_calls == 4


async def test_send_message_mixed_with_other_tool_then_text_delivers(
        anything, deliver_mock,
) -> None:
    """同轮 send_message+recall 后纯文本仍可投递。"""
    mind = _mind()
    mind._rounds = [
        tool_result("", ["send_message", "recall"]),
        text_result("混合轮后的最终答复～"),
        tool_result("", ["end_reply"]),
    ]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "混合轮后的最终答复～"


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

async def test_multi_pending_still_delivers_to_source(anything, deliver_mock) -> None:
    """存在其他待处理会话时：独白轮末仍默认投递回来源会话，不作路由询问。"""
    mind = _mind()
    mind.pfc.pending_tasks = [("group_777", "0", "777", "群消息预览")]
    mind.pfc.adapter_keys = {"group_777": "qq"}
    mind._rounds = [
        text_result("大家好！"),
        tool_result("", ["end_reply"]),
    ]
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    assert not any("路由询问" in m.get("content", "") for m in chain if m.get("role") == "system")
    deliver_mock.assert_awaited_once()
    first_deliver_target, first_content = deliver_mock.await_args.args
    assert first_deliver_target.session_key == "test:private:1"
    assert first_content == "大家好！"


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


async def test_silence_word_in_sentence_delivered(anything, deliver_mock) -> None:
    """正文中提到 [SILENT] 不触发沉默（独白轮末正常投递）。"""
    mind = _mind()
    mind._rounds = [
        text_result("我不太想用 [SILENT] 这种方式回应你"),
        tool_result("", ["end_reply"]),
    ]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    assert mind.llm_calls == 2


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
    """伪造工具调用文本：独白暂存后轮末投递点过滤，不外发。"""
    mind = _mind()
    mind._rounds = [
        text_result('[工具执行记录] send_message {"success": true}'),
        tool_result("", ["end_reply"]),
    ]
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()


# ==================================================================
# end_reply 附带正文
# ==================================================================

async def test_end_reply_content_delivered(anything, deliver_mock) -> None:
    """end_reply 同批带有 assistant 正文 → 按纯文本投递。"""
    mind = _mind()
    mind._rounds = [tool_result("这是最后一段话～", ["end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "这是最后一段话～"


async def test_end_reply_content_delivered_even_with_send_message(anything, deliver_mock) -> None:
    """同轮已有 send_message，也不抑制 end_reply 附带正文的纯文本投递。"""
    mind = _mind()
    mind._rounds = [tool_result("补充一句", ["send_message", "end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "补充一句"


async def test_end_reply_empty_content_not_delivered(anything, deliver_mock) -> None:
    """end_reply 无正文 → 不投递。"""
    mind = _mind()
    mind._rounds = [tool_result("", ["end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()


# ==================================================================
# 文本形态工具调用（弱模型把 function calling 写成文本）
# ==================================================================

class TestLooksLikeToolCallText:
    @pytest.mark.parametrize("text", [
        "end_reply()",
        'end_reply(reason=群员闲聊与我无关，静默结束)',
        'send_message(content="你好")',
    ])
    def test_detected(self, text: str) -> None:
        assert looks_like_tool_call_text(text)

    @pytest.mark.parametrize("text", [
        "这个函数 end_reply() 是用来结束对话的",  # 调用形态只是正文片段
        "我来看看（稍等）",  # 中文括号不是调用
        "今天的结论是这样。",
        "",
    ])
    def test_normal_text_not_detected(self, text: str) -> None:
        assert not looks_like_tool_call_text(text)


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


async def test_text_form_end_reply_delivers_earlier_pending(anything, deliver_mock) -> None:
    """先有正常独白、再输出文本形态 end_reply：只保底投递此前的独白。"""
    mind = _mind()
    mind._rounds = [
        text_result("这是给你的答复～"),
        text_result("end_reply()"),
    ]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "这是给你的答复～"


async def test_tool_call_shaped_text_filtered_at_delivery(anything, deliver_mock) -> None:
    """整条是字面工具调用形态的独白：轮末投递点过滤，不外发。"""
    mind = _mind()
    mind._rounds = [
        text_result('send_message(content="你好")'),
        tool_result("", ["end_reply"]),
    ]
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()
