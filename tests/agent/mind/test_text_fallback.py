"""纯文本兜底投递（think_loop）单元测试。

新行为（参考 hermes-agent 来源绑定路由：路由是系统职责，AI 无选路权）：
- AI 未调工具直接输出文字 → 系统自动投递到激活本轮的会话并结束本轮
- [SILENT] 精确匹配 → 不投递，直接结束（正文提及不误杀）
- 伪造工具调用文本 → 不投递，纠正后连续 2 次熔断
- 空输出 → 可接受，不注入纠正提示，连续 2 次安静结束
- 多候选会话 → 反问 AI 路由，纯逻辑提取；解析失败回退激活会话
- end_reply 附带文本 → 同轮无成功 send_message 时投递（同轮抑制防双发）
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

    def __init__(self, text: str = "好的，收到！", force_tool_use: bool = False) -> None:
        self.pfc = _FakePfc()
        self.compressor = None
        self._text = text
        self._rounds: List[SimpleNamespace] = []
        self.llm_calls = 0
        self.tool_choices: list = []
        self._force_tool_use = force_tool_use
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
        return SimpleNamespace(llm_timeout=10.0, force_tool_use=self._force_tool_use)

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
    """拦截纯文本投递，避免真实频道发送。"""
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
# 纯文本兜底投递
# ==================================================================

async def test_plain_text_delivered_to_active_session(anything, deliver_mock) -> None:
    """纯文本直接投递到激活会话（adapter_key + uid），一轮结束，不再纠正/重试。"""
    mind = _FakeMind("你好呀！")
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 1
    deliver_mock.assert_awaited_once()
    target, content = deliver_mock.await_args.args
    assert target.session_key == "test:private:1"
    assert content == "你好呀！"
    assert any("纯文本回复已投递" in s for s in steps)


async def test_group_text_delivered_to_group(deliver_mock) -> None:
    """群聊触发：纯文本投递到 group scope。"""
    from agent.messages import EverythingGroup

    anything = EverythingGroup(adapter_key="qq", uid=42, group_id=777, text_content="在吗")
    mind = _FakeMind("在的～")
    await _run(mind, anything)

    target, _ = deliver_mock.await_args.args
    assert target.session_key == "qq:group:777"


async def test_reply_guide_injected_each_round(anything, deliver_mock) -> None:
    """输出方式说明（事实陈述）每轮随执行上下文注入。"""
    mind = _FakeMind()
    seen: List[str] = []
    original = mind._invoke_llm_unified

    async def spy(messages, tools, anything=None, *, tool_choice=None, options=None):
        seen.append(messages[-1]["content"])
        return await original(messages, tools, anything, tool_choice=tool_choice, options=options)

    mind._invoke_llm_unified = spy
    await _run(mind, anything)

    assert seen and all("输出方式" in content for content in seen)


async def test_silent_marker_not_delivered(anything, deliver_mock) -> None:
    """[SILENT] 精确匹配：不投递，直接结束本轮。"""
    mind = _FakeMind("[SILENT]")
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 1
    deliver_mock.assert_not_awaited()
    assert any("沉默" in s for s in steps)


async def test_silent_word_in_sentence_still_delivered(anything, deliver_mock) -> None:
    """正文中提到 [SILENT] 不触发沉默（精确匹配防误杀）。"""
    mind = _FakeMind("我不太想用 [SILENT] 这种方式回应你")
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()


@pytest.mark.parametrize("narration", ["*沉默*", "（沉默）", "🔇", "…", "*(silent)*", "`silent`"])
async def test_silence_narration_not_delivered(anything, deliver_mock, narration) -> None:
    """幻觉沉默旁白（hermes 式过滤）：整条只是姿态标记 → 不投递，直接结束。"""
    mind = _FakeMind(narration)
    steps: List[str] = []
    await _run(mind, anything, steps)

    assert mind.llm_calls == 1
    deliver_mock.assert_not_awaited()
    assert any("沉默" in s for s in steps)


async def test_silence_narration_in_sentence_still_delivered(anything, deliver_mock) -> None:
    """包含"沉默"的正常句子不触发旁白过滤（锚定整条防误杀）。"""
    mind = _FakeMind("沉默是今晚的康桥，这句话出自徐志摩的诗")
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()


async def test_empty_output_quietly_ends(anything, deliver_mock) -> None:
    """空输出可接受：不注入纠正提示，连续 2 次安静结束。"""
    mind = _FakeMind("")
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    deliver_mock.assert_not_awaited()
    assert not any("禁止" in m.get("content", "") for m in chain if m.get("role") == "system")


async def test_fake_tool_call_not_delivered(anything, deliver_mock) -> None:
    """伪造工具调用文本：不投递，纠正后连续 2 次熔断结束。"""
    mind = _FakeMind('[工具执行记录] send_message {"success": true, "action": "send"}')
    steps: List[str] = []
    chain: List = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    deliver_mock.assert_not_awaited()
    assert any("系统拦截" in m.get("content", "") for m in chain if m.get("role") == "system")


# ==================================================================
# 多候选会话路由
# ==================================================================

async def test_multi_candidates_route_by_index(anything, deliver_mock) -> None:
    """多候选会话：反问 AI 路由，AI 回答编号 → 投递到所选会话。"""
    mind = _FakeMind()
    mind.pfc._pending = [("group_777", 0, 777, "群消息预览")]
    mind.pfc._adapter_keys = {"group_777": "qq"}
    mind._rounds = [_text_result("大家好！"), _text_result("2")]
    chain: List = []
    steps: List[str] = []
    await _run(mind, anything, steps, chain)

    assert mind.llm_calls == 2
    # 第一轮注入了路由询问
    assert any("路由询问" in m.get("content", "") for m in chain if m.get("role") == "system")
    # 投递的是第一轮的原文，目标是编号 2 对应的会话
    deliver_mock.assert_awaited_once()
    target, content = deliver_mock.await_args.args
    assert target.session_key == "qq:group:777"
    assert content == "大家好！"


async def test_route_parse_failure_falls_back_to_active(anything, deliver_mock) -> None:
    """路由解析失败：回退投递到激活本轮的会话。"""
    mind = _FakeMind()
    mind.pfc._pending = [("group_777", 0, 777, "群消息预览")]
    mind.pfc._adapter_keys = {"group_777": "qq"}
    mind._rounds = [_text_result("大家好！"), _text_result("嗯……随便吧")]
    steps: List[str] = []
    await _run(mind, anything, steps)

    target, content = deliver_mock.await_args.args
    assert target.session_key == "test:private:1"
    assert content == "大家好！"
    assert any("回退激活会话" in s for s in steps)


# ==================================================================
# end_reply 附带文本
# ==================================================================

async def test_end_reply_text_delivered_when_no_send(anything, deliver_mock) -> None:
    """end_reply 附带文本且本轮未发送 → 文本作为回复投递后结束。"""
    mind = _FakeMind()
    mind._rounds = [_mk_result("这就是答案啦", ["end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_awaited_once()
    _, content = deliver_mock.await_args.args
    assert content == "这就是答案啦"


async def test_end_reply_text_suppressed_when_send_succeeded(anything, deliver_mock) -> None:
    """同轮 send_message 已成功 → end_reply 附带文本不投递（防双发）。"""
    mind = _FakeMind()
    mind._rounds = [_mk_result("已发送补充说明", ["send_message", "end_reply"])]
    await _run(mind, anything)

    deliver_mock.assert_not_awaited()


# ==================================================================
# tool_choice 策略
# ==================================================================

async def test_force_tool_use_required_when_enabled(anything, deliver_mock) -> None:
    """开启纯工具模式：LLM 调用强制 tool_choice='required'。"""
    mind = _FakeMind(force_tool_use=True)
    await _run(mind, anything, tools=[{"type": "function", "function": {"name": "send_message"}}])

    assert mind.tool_choices[0] == "required"


async def test_tool_choice_auto_by_default(anything, deliver_mock) -> None:
    """默认（纯工具模式关闭）：不强制 tool_choice，纯文本可正常产出。"""
    mind = _FakeMind(force_tool_use=False)
    await _run(mind, anything, tools=[{"type": "function", "function": {"name": "send_message"}}])

    assert mind.tool_choices[0] is None
