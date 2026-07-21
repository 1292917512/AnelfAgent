"""内心独白死循环守卫（think_loop）单元测试。

复现问题场景：模型连续只输出文字（"我要用工具发消息"）却不发起工具调用，
验证连续 N 次后强制结束，而非无限循环。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import List, Optional
from unittest.mock import AsyncMock

import pytest

from agent.mind.tools.think_loop import ThinkMode, think_loop


class _FakePfc:
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


class _FakeMind:
    """最小 Mind 替身：LLM 始终返回纯文字（无工具调用）。"""

    def __init__(self, text: str = "我必须用工具发出去！", force_tool_use: bool = True) -> None:
        self.pfc = _FakePfc()
        self.compressor = None
        self._text = text
        self.llm_calls = 0
        self.tool_choices: list = []
        self._force_tool_use = force_tool_use
        self._add_system_context = AsyncMock()

    def _resolve_adapter_key(self) -> str:
        return ""

    @property
    def tool_executor(self):
        async def _exec(tc) -> str:
            return '{"ok": true}'
        return _exec

    def _set_phase(self, phase) -> None:
        pass

    def _get_mind_config(self):
        return SimpleNamespace(llm_timeout=10.0, force_tool_use=self._force_tool_use)

    def get_model_context_length(self) -> int:
        return 0

    async def _invoke_llm_unified(self, messages, tools, anything=None, *, tool_choice=None, options=None):
        self.llm_calls += 1
        self.tool_choices.append(tool_choice)
        return SimpleNamespace(
            content=self._text,
            tool_calls=[],
            reasoning_content="",
            usage=None,
            raw=None,
            model="fake",
        )


@pytest.fixture
def anything():
    return SimpleNamespace(adapter_key="test", uid=1, group_id=0)


async def test_monologue_loop_force_ends(anything) -> None:
    """连续内心独白达到上限（3 次）后强制结束，不再继续循环。"""
    mind = _FakeMind()
    steps: List[str] = []
    await think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=[],
        execution_steps=steps,
        start_time=time.time(),
        safety_limit=100,  # 高上限：证明是守卫而非上限终止循环
        collected_text=[],
        active_tools=[],
        anything=anything,
        base_messages=[{"role": "user", "content": "你好"}],
    )
    # 3 次独白后强制结束，而非跑满 100 轮
    assert mind.llm_calls == 3
    assert any("连续内心独白" in s for s in steps)


async def test_only_first_monologue_saved(anything) -> None:
    """重复独白不入库：仅首次写入历史，切断模式强化。"""
    mind = _FakeMind()
    await think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=[],
        execution_steps=[],
        start_time=time.time(),
        safety_limit=100,
        collected_text=[],
        active_tools=[],
        anything=anything,
        base_messages=[{"role": "user", "content": "你好"}],
    )
    # 3 次独白只保存 1 次
    assert mind._add_system_context.await_count == 1
    # 以 assistant 角色写入（而非 user）
    _, kwargs = mind._add_system_context.await_args
    assert kwargs.get("role") == "assistant"


async def test_tool_choice_escalation(anything) -> None:
    """纯工具模式：默认每轮都强制 tool_choice='required'。"""
    mind = _FakeMind()
    await think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=[],
        execution_steps=[],
        start_time=time.time(),
        safety_limit=100,
        collected_text=[],
        active_tools=[{"type": "function", "function": {"name": "send_message"}}],
        anything=anything,
        base_messages=[{"role": "user", "content": "你好"}],
    )
    # 纯工具模式：全部轮次强制 required
    assert all(tc == "required" for tc in mind.tool_choices)


def _spy_exec_context(mind: _FakeMind) -> List[str]:
    """包装 _invoke_llm_unified，捕获每轮末尾 exec_context 的文本。"""
    seen: List[str] = []
    original = mind._invoke_llm_unified

    async def spy(messages, tools, anything=None, *, tool_choice=None, options=None):
        seen.append(messages[-1]["content"])
        return await original(messages, tools, anything, tool_choice=tool_choice, options=options)

    mind._invoke_llm_unified = spy
    return seen


async def test_tool_output_discipline_hint_injected(anything) -> None:
    """端点不支持强制 tool_choice 时：每轮末尾注入输出纪律提示。"""
    mind = _FakeMind()
    mind.llm = SimpleNamespace(
        config=SimpleNamespace(supports_forced_tool_choice=False),
    )
    seen = _spy_exec_context(mind)
    await think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=[],
        execution_steps=[],
        start_time=time.time(),
        safety_limit=100,
        collected_text=[],
        active_tools=[{"type": "function", "function": {"name": "send_message"}}],
        anything=anything,
        base_messages=[{"role": "user", "content": "你好"}],
    )
    assert seen
    assert all("输出纪律" in content for content in seen)


async def test_tool_output_discipline_hint_skipped_when_supported(anything) -> None:
    """端点支持强制 tool_choice（默认）时：不注入输出纪律提示。"""
    mind = _FakeMind()
    seen = _spy_exec_context(mind)
    await think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=[],
        execution_steps=[],
        start_time=time.time(),
        safety_limit=100,
        collected_text=[],
        active_tools=[{"type": "function", "function": {"name": "send_message"}}],
        anything=anything,
        base_messages=[{"role": "user", "content": "你好"}],
    )
    assert seen
    assert all("输出纪律" not in content for content in seen)


async def test_pure_tool_mode_disabled(anything) -> None:
    """关闭纯工具模式后：首轮不强制，独白后升级为强制。"""
    mind = _FakeMind(force_tool_use=False)
    await think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=[],
        execution_steps=[],
        start_time=time.time(),
        safety_limit=100,
        collected_text=[],
        active_tools=[{"type": "function", "function": {"name": "send_message"}}],
        anything=anything,
        base_messages=[{"role": "user", "content": "你好"}],
    )
    # 第 1 轮无强制，独白后第 2、3 轮强制 required
    assert mind.tool_choices[0] is None
    assert mind.tool_choices[1] == "required"
    assert mind.tool_choices[2] == "required"


async def test_monologue_counter_resets_on_tool_call(anything) -> None:
    """工具调用后独白计数重置（交替场景不触发强制结束）。"""
    mind = _FakeMind()
    # 第 2 次调用返回一个工具调用，之后继续独白
    call_count = 0

    async def alternating(messages, tools, anything=None, *, tool_choice=None, options=None):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return SimpleNamespace(
                content="",
                tool_calls=[SimpleNamespace(
                    id="tc1", name="end_reply", arguments="{}",
                    raw={"id": "tc1", "type": "function", "function": {"name": "end_reply", "arguments": "{}"}},
                )],
                reasoning_content="", usage=None, raw=None, model="fake",
            )
        return SimpleNamespace(
            content=mind._text, tool_calls=[], reasoning_content="",
            usage=None, raw=None, model="fake",
        )

    mind._invoke_llm_unified = alternating
    steps: List[str] = []
    await think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=[],
        execution_steps=steps,
        start_time=time.time(),
        safety_limit=10,
        collected_text=[],
        active_tools=[],
        anything=anything,
        base_messages=[{"role": "user", "content": "你好"}],
    )
    # 第 2 轮 end_reply 正常结束，未触发独白强制结束
    assert call_count == 2
    assert not any("连续内心独白" in s for s in steps)


# ==================================================================
# 提前结束拦截：唯一动作是 end_reply 且附带大段不可见文本
# ==================================================================

_DECLARED_TOOLS = [
    {"type": "function", "function": {"name": "send_message"}},
    {"type": "function", "function": {"name": "generate_image"}},
    {"type": "function", "function": {"name": "end_reply"}},
]

_LONG_PLAN_TEXT = (
    "找到 generate_image 工具了！现在先调 generate_image 画图，"
    "拿到生成路径后再用 send_photo 发给主人，最后 send_message 收尾"
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


class _DeclaredMind(_FakeMind):
    """Mind 替身：按队列返回 文本+工具调用 组合。"""

    def __init__(self, rounds: List[SimpleNamespace]) -> None:
        super().__init__()
        self._rounds = list(rounds)
        self._reply_adapter_key = ""

    async def _invoke_llm_unified(self, messages, tools, anything=None, *, tool_choice=None, options=None):
        self.llm_calls += 1
        self.tool_choices.append(tool_choice)
        if self._rounds:
            return self._rounds.pop(0)
        return _mk_result("", ["end_reply"])


async def _run_loop(mind, anything, steps: List[str], chain: List) -> None:
    await think_loop(
        mind,
        mode=ThinkMode.REPLY,
        tool_chain=chain,
        execution_steps=steps,
        start_time=time.time(),
        safety_limit=10,
        collected_text=[],
        active_tools=list(_DECLARED_TOOLS),
        anything=anything,
        base_messages=[{"role": "user", "content": "帮我画张图"}],
    )


async def test_end_reply_intercepted_when_action_declared(anything) -> None:
    """唯一动作是 end_reply 且附带大段计划文本 → 拦截并要求兑现。"""
    mind = _DeclaredMind([
        _mk_result(_LONG_PLAN_TEXT, ["end_reply"]),
        _mk_result("", ["end_reply"]),
    ])
    steps: List[str] = []
    chain: List = []
    await _run_loop(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    assert any("结束被拦截" in s for s in steps)
    blocked = [m for m in chain if m.get("role") == "system" and "没有执行任何实际操作" in m.get("content", "")]
    assert blocked and "下一轮" in blocked[0]["content"]


async def test_end_reply_interception_capped_at_two(anything) -> None:
    """持续只说不做：拦截 2 次后第 3 次 end_reply 放行（防拦截死循环）。"""
    mind = _DeclaredMind([
        _mk_result(_LONG_PLAN_TEXT, ["end_reply"]) for _ in range(5)
    ])
    steps: List[str] = []
    await _run_loop(mind, anything, steps, [])

    assert mind.llm_calls == 3
    assert sum("结束被拦截" in s for s in steps) == 2


async def test_end_reply_honored_when_action_fulfilled(anything) -> None:
    """本轮有实际工具调用（工作工具 + end_reply）→ end_reply 直接放行。"""
    mind = _DeclaredMind([
        _mk_result(_LONG_PLAN_TEXT, ["generate_image", "end_reply"]),
    ])
    steps: List[str] = []
    await _run_loop(mind, anything, steps, [])

    assert mind.llm_calls == 1
    assert not any("结束被拦截" in s for s in steps)


async def test_end_reply_honored_with_short_text(anything) -> None:
    """简短收尾文本 + end_reply → 直接放行（不足判定阈值）。"""
    mind = _DeclaredMind([
        _mk_result("好的，结束啦", ["end_reply"]),
    ])
    steps: List[str] = []
    await _run_loop(mind, anything, steps, [])

    assert mind.llm_calls == 1
    assert not any("结束被拦截" in s for s in steps)
