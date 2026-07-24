"""纯文本终态投递 + 多频道路由（think_loop）单元测试。

对齐 Hermes：无工具正文 = 最终回复，系统投递一次后结束本轮。
路由：单候选（同源私聊/群）直回；多候选时问 AI 一轮再投递结束。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock

import pytest

from agent.mind.tools import think_loop as tl
from agent.mind.tools.think_loop import ThinkMode, think_loop


class _FakePfc:
    def __init__(self) -> None:
        self._pending: list = []
        self._adapter_keys: dict = {}

    def build_execution_context(self, *a, **kw) -> dict:
        return {"role": "system", "content": "exec"}

    def add_temporary(self, clip) -> None:
        pass

    def clear_dynamic_tools(self) -> None:
        pass

    def record_tool_use(self, name: str) -> None:
        pass

    def expand_discovered_tools(self, tool_calls) -> None:
        pass

    def peek_all_tasks(self) -> list:
        return list(self._pending)

    def get_adapter_key(self, scope: str) -> str:
        return self._adapter_keys.get(scope, "")


def _text_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=text, tool_calls=[], reasoning_content="",
        usage=None, raw=None, model="fake",
    )


def _mk_result(text: str, tool_names: List[str]) -> SimpleNamespace:
    return SimpleNamespace(
        content=text,
        tool_calls=[
            SimpleNamespace(
                id=f"tc_{n}", name=n, arguments="{}",
                raw={"id": f"tc_{n}", "type": "function",
                     "function": {"name": n, "arguments": "{}"}},
            )
            for n in tool_names
        ],
        reasoning_content="", usage=None, raw=None, model="fake",
    )


class _FakeMind:
    """最小 Mind 替身：LLM 按队列返回结果（默认持续返回同一文本）。"""

    def __init__(self, text: str = "我先说两句～") -> None:
        self.pfc = _FakePfc()
        self.compressor = None
        self._text = text
        self._rounds: List[SimpleNamespace] = []
        self.llm_calls = 0
        self.tool_choices: list = []
        self._add_system_context = AsyncMock()
        self._reply_adapter_key = ""

    def _resolve_adapter_key(self) -> str:
        return ""

    @property
    def tool_executor(self):
        async def _exec(tc) -> str:
            if tc.name == "send_message":
                return '{"success": true, "target_id": "1", "message_id": "m1"}'
            return '{"ok": true}'
        return _exec

    def _set_phase(self, phase) -> None:
        pass

    def _get_mind_config(self):
        return SimpleNamespace(
            llm_timeout=10.0, force_tool_use=False,
            text_without_tool_limit=5,
            background_wait_timeout=30.0, background_wait_budget=120.0,
        )

    def get_model_context_length(self) -> int:
        return 0

    async def _invoke_llm_unified(self, messages, tools, anything=None, *, tool_choice=None, options=None):
        self.llm_calls += 1
        self.tool_choices.append(tool_choice)
        if self._rounds:
            return self._rounds.pop(0)
        return _text_result(self._text)


@pytest.fixture
def anything():
    return SimpleNamespace(adapter_key="test", uid=1, group_id=0)


@pytest.fixture
def deliver_mock(monkeypatch):
    """拦截纯文本投递。"""
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(tl, "deliver_text", mock)
    return mock


def _run(mind, anything, steps=None, chain=None, tools=None):
    return think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=chain if chain is not None else [],
        execution_steps=steps if steps is not None else [],
        start_time=time.time(),
        safety_limit=20,
        collected_text=[],
        active_tools=tools if tools is not None else [],
        anything=anything,
        base_messages=[{"role": "user", "content": "你好"}],
    )


# ==================================================================
# 纯文本终态投递（Hermes：代发一次后结束）
# ==================================================================

async def test_bare_text_delivered_and_ends(anything, deliver_mock) -> None:
    """单候选：纯文本投递到来源会话后结束本轮。"""
    mind = _FakeMind()
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_awaited_once()
    target, content = deliver_mock.await_args.args
    assert target.session_key == "test:private:1"
    assert content == "我先说两句～"
    assert mind.llm_calls == 1
    assert any("本轮结束" in s for s in steps)


async def test_same_group_direct_reply(deliver_mock) -> None:
    """同源群消息：直接回到该群，不问 AI。"""
    from agent.messages.everything import EverythingGroup

    anything = EverythingGroup(adapter_key="qq", uid=42, group_id=777, text_content="hi")
    mind = _FakeMind(text="群里见～")
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    deliver_mock.assert_awaited_once()
    target, content = deliver_mock.await_args.args
    assert target.session_key == "qq:group:777"
    assert content == "群里见～"
    assert not any("路由询问" in m.get("content", "") for m in chain if m.get("role") == "system")
    assert mind.llm_calls == 1


async def test_bare_text_no_continue_or_sent_ack(anything, deliver_mock) -> None:
    """终态后不再注入「未调工具」催促或「已发送」假 assistant。"""
    mind = _FakeMind()
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
    mind = _FakeMind()
    mind._rounds = [
        _mk_result("", ["recall"]),
        _mk_result("", ["end_reply"]),
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
    mind = _FakeMind()
    mind._rounds = [
        _mk_result("你好", ["send_message"]),
        _mk_result("", ["end_reply"]),
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


async def test_tool_then_bare_text_ends(anything, deliver_mock) -> None:
    """非输出工具后输出最终纯文本：投递一次并结束。"""
    mind = _FakeMind()
    mind._rounds = [
        _mk_result("", ["recall"]),
        _text_result("查到了，结果是这样～"),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_awaited_once()
    assert mind.llm_calls == 2
    assert any("本轮结束" in s for s in steps)


async def test_send_message_then_bare_text_skips_deliver(anything, deliver_mock) -> None:
    """仅 send_message 成功后紧跟纯文本：不再代发，直接结束。"""
    mind = _FakeMind()
    mind._rounds = [
        _mk_result("", ["send_message"]),
        _text_result("再说一遍会重复～"),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_not_awaited()
    assert mind.llm_calls == 2
    assert any("跳过投递" in s for s in steps)


async def test_send_message_then_other_tool_then_text_delivers(
        anything, deliver_mock,
) -> None:
    """send_message 后再调其他工具，随后纯文本仍可投递（非紧邻输出类）。"""
    mind = _FakeMind()
    mind._rounds = [
        _mk_result("", ["send_message"]),
        _mk_result("", ["recall"]),
        _text_result("补充最终结论～"),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "补充最终结论～"
    assert mind.llm_calls == 3


async def test_send_message_mixed_with_other_tool_then_text_delivers(
        anything, deliver_mock,
) -> None:
    """同轮 send_message+recall 后纯文本仍可投递（不是「仅输出类」）。"""
    mind = _FakeMind()
    mind._rounds = [
        _mk_result("", ["send_message", "recall"]),
        _text_result("混合轮后的最终答复～"),
    ]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "混合轮后的最终答复～"


async def test_bare_text_no_thought_label(anything, deliver_mock) -> None:
    """纯文本不应以 '[思维]' 标签入库。"""
    mind = _FakeMind()
    await _run(mind, anything)

    thought_labels = [
        c for c in mind._add_system_context.await_args_list
        if "[思维]" in (c.kwargs.get("content") or (c.args[1] if len(c.args) > 1 else ""))
    ]
    assert not thought_labels


# ==================================================================
# 多候选路由
# ==================================================================

async def test_multi_candidates_route_by_index(anything, deliver_mock) -> None:
    """多候选：反问 AI → 投递到所选会话 → 本轮结束。"""
    mind = _FakeMind()
    mind.pfc._pending = [("group_777", 0, 777, "群消息预览")]
    mind.pfc._adapter_keys = {"group_777": "qq"}
    mind._rounds = [
        _text_result("大家好！"),
        _text_result("2"),
    ]
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    assert any("路由询问" in m.get("content", "") for m in chain if m.get("role") == "system")
    assert any("群消息预览" in m.get("content", "") for m in chain if m.get("role") == "system")
    deliver_mock.assert_awaited_once()
    first_deliver_target, first_content = deliver_mock.await_args.args
    assert first_deliver_target.session_key == "qq:group:777"
    assert first_content == "大家好！"
    assert any("本轮结束" in s for s in steps)


async def test_route_parse_failure_falls_back(anything, deliver_mock) -> None:
    """路由解析失败：回退到来源会话投递后结束。"""
    mind = _FakeMind()
    mind.pfc._pending = [("group_777", 0, 777, "群消息预览")]
    mind.pfc._adapter_keys = {"group_777": "qq"}
    mind._rounds = [
        _text_result("大家好！"),
        _text_result("嗯……随便吧"),
    ]
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    deliver_mock.assert_awaited_once()
    first_deliver_target, first_content = deliver_mock.await_args.args
    assert first_deliver_target.session_key == "test:private:1"
    assert first_content == "大家好！"
    assert any("回退来源会话" in s for s in steps)
    assert any("本轮结束" in s for s in steps)


# ==================================================================
# 沉默/伪造/空输出
# ==================================================================

async def test_silent_marker_ends_turn(anything, deliver_mock) -> None:
    """[SILENT] 精确匹配：不投递，直接结束。"""
    mind = _FakeMind(text="[SILENT]")
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 1
    deliver_mock.assert_not_awaited()
    assert any("沉默" in s for s in steps)


@pytest.mark.parametrize("narration", ["*沉默*", "（沉默）", "🔇", "…", "*(silent)*"])
async def test_silence_narration_ends_turn(anything, deliver_mock, narration) -> None:
    """幻觉沉默旁白：不投递，直接结束。"""
    mind = _FakeMind(text=narration)
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 1
    deliver_mock.assert_not_awaited()


async def test_silence_word_in_sentence_delivered(anything, deliver_mock) -> None:
    """正文中提到 [SILENT] 不触发沉默（正常投递并结束）。"""
    mind = _FakeMind(text="我不太想用 [SILENT] 这种方式回应你")
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    assert mind.llm_calls == 1


async def test_empty_output_quietly_ends(anything, deliver_mock) -> None:
    """空输出可接受，不注入纠正提示，连续 2 次安静结束。"""
    mind = _FakeMind(text="")
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    deliver_mock.assert_not_awaited()
    assert not any("禁止" in m.get("content", "") for m in chain if m.get("role") == "system")


async def test_fake_tool_call_not_delivered(anything, deliver_mock) -> None:
    """伪造工具调用文本：不投递，提示纠正。"""
    mind = _FakeMind(text='[工具执行记录] send_message {"success": true}')
    mind._rounds = [
        _text_result('[工具执行记录] send_message {"success": true}'),
        _mk_result("", ["end_reply"]),
    ]
    chain: List = []
    await _run(mind, anything, chain=chain)

    blocked = [m for m in chain if m.get("role") == "system" and "系统拦截" in m.get("content", "")]
    assert blocked
    deliver_mock.assert_not_awaited()


# ==================================================================
# end_reply 附带正文
# ==================================================================

async def test_end_reply_content_delivered(anything, deliver_mock) -> None:
    """end_reply 同批带有 assistant 正文 → 按纯文本投递。"""
    mind = _FakeMind()
    mind._rounds = [_mk_result("这是最后一段话～", ["end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "这是最后一段话～"


async def test_end_reply_content_delivered_even_with_send_message(anything, deliver_mock) -> None:
    """同轮已有 send_message，也不抑制 end_reply 附带正文的纯文本投递。"""
    mind = _FakeMind()
    mind._rounds = [_mk_result("补充一句", ["send_message", "end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "补充一句"


async def test_end_reply_empty_content_not_delivered(anything, deliver_mock) -> None:
    """end_reply 无正文 → 不投递。"""
    mind = _FakeMind()
    mind._rounds = [_mk_result("", ["end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()
