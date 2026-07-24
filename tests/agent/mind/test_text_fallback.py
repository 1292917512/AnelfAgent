"""纯文本兜底投递 + 继续循环（think_loop）单元测试。

主路径期望走 send_message / 工具；纯文本是无奈兜底：系统代发后继续循环提醒，
无后续则 end_reply / [SILENT]；连续纯文本达上限熔断。多候选时反问路由。
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

    def __init__(self, text: str = "我先说两句～", limit: int = 5) -> None:
        self.pfc = _FakePfc()
        self.compressor = None
        self._text = text
        self._rounds: List[SimpleNamespace] = []
        self.llm_calls = 0
        self.tool_choices: list = []
        self._limit = limit
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
            text_without_tool_limit=self._limit,
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
# 纯文本自动投递（已发给用户 + 继续循环 + 上限熔断）
# ==================================================================

async def test_bare_text_delivered_and_continues(anything, deliver_mock) -> None:
    """纯文本投递到激活会话，且不结束本轮。"""
    mind = _FakeMind(limit=3)
    steps: List[str] = []
    await _run(mind, anything, steps)

    deliver_mock.assert_awaited()
    target, content = deliver_mock.await_args.args
    assert target.session_key == "test:private:1"
    assert content == "我先说两句～"
    assert any("本轮继续" in s for s in steps)


async def test_bare_text_loop_hits_limit(anything, deliver_mock) -> None:
    """达到 text_without_tool_limit 后强制结束本轮。"""
    mind = _FakeMind(limit=3)
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 3
    assert any("熔断结束" in s for s in steps)


async def test_bare_text_injects_continue_hint(anything, deliver_mock) -> None:
    """纯文本后注入 assistant「已发送」确认 + system 未调工具提醒（确认不入库）。"""
    mind = _FakeMind(limit=5)
    chain: List = []
    await _run(mind, anything, chain=chain)

    sent_marks = [
        m for m in chain if m.get("role") == "assistant"
        and "已发送给用户" in m.get("content", "")
    ]
    assert sent_marks
    reminders = [
        m for m in chain if m.get("role") == "system"
        and "未调用工具" in m.get("content", "")
        and ("end_reply" in m.get("content", "") or "[SILENT]" in m.get("content", ""))
    ]
    assert reminders
    guide_and_hints = "\n".join(
        m.get("content", "") for m in chain if m.get("role") == "system"
    )
    assert "代为发送" not in guide_and_hints
    assert "直接输出纯文本" not in guide_and_hints
    assert "系统会代为发送" not in guide_and_hints


async def test_non_output_tools_inject_visibility_hint(anything, deliver_mock) -> None:
    """查资料类工具后注入「结果仅你可见，请用 send_message」。"""
    mind = _FakeMind(limit=5)
    mind._rounds = [
        _mk_result("", ["recall"]),
        _mk_result("", ["end_reply"]),
    ]
    chain: List = []
    await _run(mind, anything, chain=chain)

    hints = [
        m for m in chain if m.get("role") == "system"
        and "仅你可见" in m.get("content", "")
        and "send_message" in m.get("content", "")
    ]
    assert hints


async def test_send_message_injects_sent_mark(anything, deliver_mock) -> None:
    """send_message 成功后注入 assistant「已发送」确认（仅 tool_chain，不入库）。"""
    mind = _FakeMind(limit=5)
    mind._rounds = [
        _mk_result("你好", ["send_message"]),
        _mk_result("", ["end_reply"]),
    ]
    chain: List = []
    await _run(mind, anything, chain=chain)

    sent_marks = [
        m for m in chain if m.get("role") == "assistant"
        and "已发送给用户" in m.get("content", "")
    ]
    assert sent_marks
    # 已走输出工具，不应再注入「仅你可见」
    assert not any(
        "仅你可见" in m.get("content", "")
        for m in chain if m.get("role") == "system"
    )

async def test_bare_text_count_resets_on_tool_call(anything, deliver_mock) -> None:
    """工具调用出现时清零计数，熔断链中断。"""
    mind = _FakeMind(limit=3)
    mind._rounds = [
        _text_result("我先想想"),
        _text_result("嗯，让我再看看"),
        _mk_result("好的，主人！", ["send_message"]),
        _text_result("再想想"),
        _text_result("还是想不出"),
        _text_result("算了吧"),
    ]
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 6
    assert any("熔断结束" in s for s in steps)


async def test_bare_text_no_thought_label(anything, deliver_mock) -> None:
    """纯文本不应以 '[思维]' 标签入库（已通过 deliver_text 以 assistant 原文本入库）。"""
    mind = _FakeMind(limit=2)
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
    """多候选会话：反问 AI 路由 → 投递到所选会话（本轮继续）。"""
    mind = _FakeMind()
    mind.pfc._pending = [("group_777", 0, 777, "群消息预览")]
    mind.pfc._adapter_keys = {"group_777": "qq"}
    mind._rounds = [
        _text_result("大家好！"),
        _text_result("2"),
        _text_result("好吧"),
    ]
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls >= 2
    assert any("路由询问" in m.get("content", "") for m in chain if m.get("role") == "system")
    first_deliver_target, first_content = deliver_mock.await_args_list[0].args
    assert first_deliver_target.session_key == "qq:group:777"
    assert first_content == "大家好！"


async def test_route_parse_failure_falls_back(anything, deliver_mock) -> None:
    """路由解析失败：回退到激活会话投递。"""
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

    first_deliver_target, first_content = deliver_mock.await_args_list[0].args
    assert first_deliver_target.session_key == "test:private:1"
    assert first_content == "大家好！"
    assert any("回退激活会话" in s for s in steps)


# ==================================================================
# 沉默/伪造/空输出
# ==================================================================

async def test_silent_marker_ends_turn(anything, deliver_mock) -> None:
    """[SILENT] 精确匹配：不投递，直接结束。"""
    mind = _FakeMind(text="[SILENT]", limit=5)
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 1
    deliver_mock.assert_not_awaited()
    assert any("沉默" in s for s in steps)


@pytest.mark.parametrize("narration", ["*沉默*", "（沉默）", "🔇", "…", "*(silent)*"])
async def test_silence_narration_ends_turn(anything, deliver_mock, narration) -> None:
    """幻觉沉默旁白：不投递，直接结束。"""
    mind = _FakeMind(text=narration, limit=5)
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 1
    deliver_mock.assert_not_awaited()


async def test_silence_word_in_sentence_delivered(anything, deliver_mock) -> None:
    """正文中提到 [SILENT] 不触发沉默（正常投递）。"""
    mind = _FakeMind(text="我不太想用 [SILENT] 这种方式回应你", limit=2)
    await _run(mind, anything)

    deliver_mock.assert_awaited()


async def test_empty_output_quietly_ends(anything, deliver_mock) -> None:
    """空输出可接受，不注入纠正提示，连续 2 次安静结束。"""
    mind = _FakeMind(text="", limit=5)
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    deliver_mock.assert_not_awaited()
    assert not any("禁止" in m.get("content", "") for m in chain if m.get("role") == "system")


async def test_fake_tool_call_not_delivered(anything, deliver_mock) -> None:
    """伪造工具调用文本：不投递，提示纠正。"""
    mind = _FakeMind(text='[工具执行记录] send_message {"success": true}', limit=5)
    chain: List = []
    await _run(mind, anything, chain=chain)

    blocked = [m for m in chain if m.get("role") == "system" and "系统拦截" in m.get("content", "")]
    assert blocked
    deliver_mock.assert_not_awaited()


# ==================================================================
# end_reply 附带正文：按纯文本投递（与同轮是否 send_message 无关）
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


# ==================================================================
# 配置可调
# ==================================================================

async def test_custom_limit(anything, deliver_mock) -> None:
    """配置上限=2：第 2 次纯文本后熔断。"""
    mind = _FakeMind(limit=2)
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 2
    assert any("熔断结束" in s for s in steps)
