"""keyless 快照回放测试 — 锁定回复主路径的行为回归（对齐 dsh keyless snapshot）。

无需 API key、不触网络：脚本化的 LLM 轮次队列即"录制"，测试驱动真实
think_loop 回放并断言三件套——
  1. sent_messages 布局（_layer 序列 / 消息数 / 角色序列）
  2. 工具调用序列（名称 + 顺序，经 fake tool_executor 捕获）
  3. 最终出站文本（deliver_text mock 捕获）

任何改动 think_loop / 上下文组装 / 出站路由的 PR，若改变了模型可见行为，
这里的断言会红——迫使作者在 PR 里解释"行为为什么该变"。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock

import pytest

from agent.mind.tools import think_loop as tl
from agent.mind.tools.think_loop import ThinkMode, think_loop


def _tool_round(name: str, arguments: str = "{}") -> SimpleNamespace:
    """一轮 LLM 响应：发起单个工具调用。"""
    return SimpleNamespace(
        content="", reasoning_content="", usage=None, raw=None, model="fake",
        tool_calls=[SimpleNamespace(
            id=f"tc_{name}", name=name, arguments=arguments,
            raw={"id": f"tc_{name}", "type": "function",
                 "function": {"name": name, "arguments": arguments}},
        )],
    )


def _text_round(text: str) -> SimpleNamespace:
    """一轮 LLM 响应：纯文本（无工具调用 → 收敛回复）。"""
    return SimpleNamespace(
        content=text, tool_calls=[], reasoning_content="",
        usage=None, raw=None, model="fake",
    )


class _ReplayPfc:
    def build_execution_context(self, *a, **kw) -> dict:
        # 对齐真实 build_execution_context：返回带 _layer 标签的执行上下文
        return {"role": "system", "content": "exec", "_layer": "exec_context"}

    def add_temporary(self, clip) -> None:
        pass

    def clear_dynamic_tools(self) -> None:
        pass

    def record_tool_use(self, name: str) -> None:
        pass

    def expand_discovered_tools(self, tool_calls) -> None:
        pass

    def peek_all_tasks(self) -> list:
        return []

    def get_adapter_key(self, scope: str) -> str:
        return ""


class _ReplayMind:
    """脚本化 Mind 替身：按 _rounds 队列逐轮响应，捕获 sent_messages 与工具执行。"""

    def __init__(self, rounds: List[SimpleNamespace]) -> None:
        self.pfc = _ReplayPfc()
        self.compressor = None
        self._rounds = list(rounds)
        self.sent_messages: List[list] = []
        self.executed_tools: List[str] = []
        self._add_system_context = AsyncMock()
        self._reply_adapter_key = ""

    def _resolve_adapter_key(self) -> str:
        return ""

    @property
    def tool_executor(self):
        async def _exec(tc) -> str:
            self.executed_tools.append(tc.name)
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

    async def _invoke_llm_unified(self, messages, tools, anything=None, *,
                                  tool_choice=None, options=None, **_kw):
        self.sent_messages.append(list(messages))
        return self._rounds.pop(0)


@pytest.fixture
def anything():
    from agent.messages import MessageUser
    return MessageUser(uid=1)


@pytest.fixture
def deliver_mock(monkeypatch):
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(tl, "deliver_text", mock)
    return mock


def _base() -> list:
    return [
        {"role": "system", "content": "人设", "_layer": "stable"},
        {"role": "user", "content": "你好", "_layer": "conversation"},
    ]


class TestScenarioPureText:
    """场景 1：纯文本回复（单轮收敛）。"""

    async def test_replay(self, anything, deliver_mock) -> None:
        mind = _ReplayMind([_text_round("你好呀～")])
        await think_loop(
            mind, mode=ThinkMode.REPLY, tool_chain=[], execution_steps=[],
            start_time=time.time(), safety_limit=20, collected_text=[],
            active_tools=[], anything=anything, base_messages=_base(),
            adapter_key="test",
        )
        # 布局：单轮调用，base(2) + exec_context(1)
        assert len(mind.sent_messages) == 1
        round1 = mind.sent_messages[0]
        assert [m.get("_layer") for m in round1[:2]] == ["stable", "conversation"]
        assert round1[-1].get("_layer") == "exec_context"
        # 无工具执行
        assert mind.executed_tools == []
        # 出站文本
        deliver_mock.assert_awaited()
        assert deliver_mock.await_args.args[0] == "你好呀～" or \
            "你好呀～" in str(deliver_mock.await_args.args)


class TestScenarioSingleTool:
    """场景 2：单工具调用后文本收敛。"""

    async def test_replay(self, anything, deliver_mock) -> None:
        mind = _ReplayMind([_tool_round("recall"), _text_round("查到了")])
        await think_loop(
            mind, mode=ThinkMode.REPLY, tool_chain=[], execution_steps=[],
            start_time=time.time(), safety_limit=20, collected_text=[],
            active_tools=[], anything=anything, base_messages=_base(),
            adapter_key="test",
        )
        # 两轮 LLM 调用
        assert len(mind.sent_messages) == 2
        # 工具序列：recall 恰好执行一次
        assert mind.executed_tools == ["recall"]
        # 第 2 轮上下文含第 1 轮的 assistant + tool 结果（工具链并入）
        roles2 = [m.get("role") for m in mind.sent_messages[1]]
        assert "tool" in roles2
        # 出站文本
        assert deliver_mock.await_count == 1


class TestScenarioMultiToolChain:
    """场景 3：多轮工具链（顺序保持）。"""

    async def test_replay(self, anything, deliver_mock) -> None:
        mind = _ReplayMind([
            _tool_round("recall"),
            _tool_round("get_conversation"),
            _text_round("都办好了"),
        ])
        await think_loop(
            mind, mode=ThinkMode.REPLY, tool_chain=[], execution_steps=[],
            start_time=time.time(), safety_limit=20, collected_text=[],
            active_tools=[], anything=anything, base_messages=_base(),
            adapter_key="test",
        )
        # 三轮 LLM 调用
        assert len(mind.sent_messages) == 3
        # 工具序列按轮次顺序执行
        assert mind.executed_tools == ["recall", "get_conversation"]
        # 最终出站
        assert deliver_mock.await_count == 1
