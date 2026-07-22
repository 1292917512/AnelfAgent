"""B1 流式内核测试：增量聚合 / on_delta 上报 / 失败回退非流式。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import List

import pytest

from agent.llm.llm_client import LLMClient
from agent.llm.types import ChatResult, ChatStreamDelta, ToolCall, UsageInfo
from agent.mind.mind import Mind


def _fake_stream_client(deltas: List[ChatStreamDelta], raise_mid: bool = False):
    """构造绕过 __init__ 的 LLMClient 替身（isinstance 检查通过）。"""
    client = object.__new__(LLMClient)
    client.config = SimpleNamespace(model="fake-stream", name="fake")

    async def chat_stream(messages, *, options=None, tools=None, tool_choice=None):
        for i, d in enumerate(deltas):
            if raise_mid and i == 1:
                raise RuntimeError("流式中途断开")
            yield d

    client.chat_stream = chat_stream
    return client


def _mind_stub(client) -> SimpleNamespace:
    return SimpleNamespace(
        llm=client,
        _session_llm_params={},
        _get_mind_config=lambda: SimpleNamespace(reasoning_effort=None, llm_timeout=30),
    )


class TestStreamAggregation:
    async def test_content_and_reasoning_aggregated(self):
        deltas = [
            ChatStreamDelta(content="你"),
            ChatStreamDelta(content="好", reasoning_content="想"),
            ChatStreamDelta(content="吗", reasoning_content="想", finish_reason="stop",
                            usage=UsageInfo(prompt_tokens=10, completion_tokens=3, total_tokens=13)),
        ]
        mind = _mind_stub(_fake_stream_client(deltas))
        received: List[tuple] = []

        async def on_delta(delta: str, reasoning: bool):
            received.append((delta, reasoning))

        result = await Mind._llm_chat_stream_once(mind, [{"role": "user", "content": "hi"}], None,
                                                  on_delta=on_delta)
        assert result.content == "你好吗"
        assert result.reasoning_content == "想想"
        assert result.finish_reason == "stop"
        assert result.usage.total_tokens == 13
        assert ("你", False) in received and ("想", True) in received

    def test_tool_call_raw_is_wire_format(self):
        """流式 tool_calls 的 raw 必须是完整线格式（think_loop 拼装 assistant 历史依赖）。"""
        tcs = LLMClient._complete_tool_buffers({
            0: {"id": "call_abc", "name": "read_file", "arguments": '{"path":"a"}'},
        })
        raw = tcs[0].raw
        assert raw["id"] == "call_abc"
        assert raw["type"] == "function"
        assert raw["function"]["name"] == "read_file"
        assert raw["function"]["arguments"] == '{"path":"a"}'

    async def test_tool_calls_collected(self):
        tc = ToolCall(id="c1", name="read_file", arguments="{}")
        deltas = [ChatStreamDelta(tool_calls=[tc], finish_reason="tool_calls")]
        mind = _mind_stub(_fake_stream_client(deltas))
        result = await Mind._llm_chat_stream_once(mind, [], None)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "read_file"

    async def test_no_delta_callback_ok(self):
        mind = _mind_stub(_fake_stream_client([ChatStreamDelta(content="x")]))
        result = await Mind._llm_chat_stream_once(mind, [], None)
        assert result.content == "x"


class TestStreamFallback:
    async def test_mid_stream_failure_falls_back(self, monkeypatch):
        """流式中途失败 → _invoke_llm_unified(stream=True) 回退非流式。"""
        client = _fake_stream_client(
            [ChatStreamDelta(content="半截"), ChatStreamDelta(content="丢弃")],
            raise_mid=True,
        )
        fallback_result = ChatResult(content="完整结果", finish_reason="stop")

        captured = {}
        mind = _mind_stub(client)
        mind._get_mind_config = lambda: SimpleNamespace(
            reasoning_effort=None, llm_timeout=30, log_ai_output=False)
        mind.llm_manager = None

        async def fake_retry(messages, tools, *, tool_choice=None, options=None):
            captured["called"] = True
            return fallback_result

        mind._llm_chat_with_retry = fake_retry
        monkeypatch.setattr("agent.mind.mind.normalize_for_send", lambda m: m)
        monkeypatch.setattr("agent.mind.mind.context_audit", SimpleNamespace(
            record_exchange=lambda **kw: asyncio.sleep(0)))

        result = await Mind._invoke_llm_unified(mind, [{"role": "user", "content": "hi"}], None,
                                                stream=True)
        assert captured.get("called")
        assert result.content == "完整结果"
