"""LLM_END 事件的思考全文载荷契约。

思考全文仅供思维链面板展示：有界截断后驻留 tracer 内存会话
（max_sessions 滚动销毁），不落库、不注入 LLM 上下文。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.llm.llm_client import LLMClient
from agent.llm.types import ChatResult, UsageInfo
from agent.mind.llm_invoker import _REASONING_TRACE_MAX, ModelInvoker
from core.event_bus import EVENT_THINKING_LLM_END, EVENT_THINKING_LLM_START, event_bus
from core.tool_context import tool_request


def _result(reasoning: str) -> ChatResult:
    return ChatResult(
        content="答",
        reasoning_content=reasoning,
        model="fake",
        finish_reason="stop",
        usage=UsageInfo(prompt_tokens=5, completion_tokens=2, total_tokens=7),
    )


def _invoker(result: ChatResult) -> ModelInvoker:
    client = object.__new__(LLMClient)
    client.config = SimpleNamespace(model="fake", name="fake", litellm_model="", api_type="")

    async def _chat(messages, tools, **kwargs):
        return result

    mind = SimpleNamespace(
        llm=client,
        config=SimpleNamespace(
            reasoning_effort=None, llm_timeout=30, log_ai_output=False,
        ),
    )
    invoker = ModelInvoker(mind)
    invoker.chat_with_retry = _chat
    invoker.get_context_length = lambda: 128000
    return invoker


async def _capture(result: ChatResult) -> dict:
    seen: list[dict] = []
    starts: list[dict] = []

    async def _on_end(payload):
        seen.append(payload)

    async def _on_start(payload):
        starts.append(payload)

    event_bus.on(EVENT_THINKING_LLM_END, _on_end, owner="test:reasoning-trace")
    event_bus.on(EVENT_THINKING_LLM_START, _on_start, owner="test:reasoning-trace")
    try:
        await _invoker(result).invoke( [{"role": "user", "content": "hi"}], None)
    finally:
        event_bus.off_by_owner("test:reasoning-trace")
    assert seen, "LLM_END 事件未发射"
    assert starts[-1]["call_id"] == seen[-1]["call_id"]
    assert seen[-1]["call_id"]
    return seen[-1]


class TestReasoningTracePayload:
    @pytest.mark.parametrize("stream", [False, True])
    async def test_snapshot_uses_response_and_event_identity(self, monkeypatch, tmp_path, stream):
        from agent.context import snapshot as snapshots

        monkeypatch.setattr(snapshots, "_SNAPSHOT_DIR", str(tmp_path))
        observer = snapshots.ContextSnapshot()
        monkeypatch.setattr(snapshots, "context_snapshot", observer)
        await observer.arm()
        result = _result("")
        result.model = "actual-fallback-model"
        result.usage = UsageInfo(prompt_tokens=100, cache_read_input_tokens=80)
        invoker = _invoker(result)
        sent = []

        async def chat(messages, tools, **kwargs):
            sent.append((messages, tools))
            assert observer.get()["cache"]["status"] == "pending"
            return result

        invoker.chat_with_retry = chat
        invoker.chat_stream_once = chat
        ends = []
        async def on_end(payload):
            ends.append(payload)

        event_bus.on(EVENT_THINKING_LLM_END, on_end, owner="test:snapshot-response")
        messages = [{"role": "system", "content": "stable", "_layer": "stable"},
                    {"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
        try:
            returned = await invoker.invoke(messages, tools, stream=stream, purpose="reflect")
        finally:
            event_bus.off_by_owner("test:snapshot-response")
        assert returned is result
        data = observer.get()
        assert data["call_id"] == ends[0]["call_id"] == data["cache"]["call"]["call_id"]
        assert data["model"] == "actual-fallback-model"
        assert data["cache"]["call"]["kind"] == "reflect"
        assert data["cache"]["call"]["cache_hit_rate"] == ends[0]["usage"]["cache_hit_rate"] == 0.8
        assert sent[0][1] == tools
        assert all("call_id" not in message for message in sent[0][0])
        assert messages[0]["_layer"] == "stable"

    @pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
    async def test_snapshot_closes_on_failure_or_cancellation(self, monkeypatch, tmp_path, error):
        from agent.context import snapshot as snapshots

        monkeypatch.setattr(snapshots, "_SNAPSHOT_DIR", str(tmp_path))
        observer = snapshots.ContextSnapshot()
        monkeypatch.setattr(snapshots, "context_snapshot", observer)
        await observer.arm()
        invoker = _invoker(_result(""))

        async def fail(*args, **kwargs):
            raise error

        invoker.chat_with_retry = fail
        with pytest.raises(type(error)):
            await invoker.invoke([{"role": "user", "content": "hi"}], None)
        assert observer.get()["cache"]["status"] == ("cancelled" if isinstance(error, asyncio.CancelledError) else "failed")
        assert observer.get()["cache"]["call"] is None

    async def test_snapshot_disk_failure_does_not_fail_invocation(self, monkeypatch, tmp_path):
        from agent.context import snapshot as snapshots

        monkeypatch.setattr(snapshots, "_SNAPSHOT_DIR", str(tmp_path))
        observer = snapshots.ContextSnapshot()
        monkeypatch.setattr(snapshots, "context_snapshot", observer)
        def fail(*args):
            raise OSError("disk unavailable")
        monkeypatch.setattr(snapshots, "atomic_write_text", fail)
        await observer.arm()
        payload = await _capture(_result(""))
        assert observer.get()["cache"]["status"] == "completed"
        assert observer.get()["call_id"] == payload["call_id"]

    async def test_hook_snapshot_preserves_layers_without_expanding_trace(self, monkeypatch):
        from core.trace_session import thinking_session
        from core.tracer import NodeType, Tracer

        monkeypatch.setattr("agent.mind.llm_invoker._llm_end_transcript_hook_registered", lambda: True)
        messages = [{"role": "system", "content": "stable", "_layer": "stable"},
                    {"role": "user", "content": "review"}]
        tools = [{"type": "function", "function": {"name": "get_skill", "parameters": {"type": "object"}}}]
        seen = []
        async def capture(payload):
            seen.append(payload)
        tracer = Tracer()
        tracer.set_enabled(True)
        queue = tracer.subscribe()
        event_bus.on(EVENT_THINKING_LLM_END, capture, owner="test:hook-snapshot")
        try:
            async with thinking_session({"scope": "user_qq:1"}):
                await _invoker(_result("thought")).invoke( messages, tools)
            assert seen[-1]["messages"] == messages
            assert seen[-1]["tools"] == tools
            session = next(iter(tracer._sessions.values()))
            node = next(node for node in session.nodes if node.type == NodeType.LLM_CALL)
            assert "messages" not in node.data and "tools" not in node.data
        finally:
            event_bus.off_by_owner("test:hook-snapshot")
            tracer.unsubscribe(queue)
            tracer.set_enabled(False)

    async def test_request_and_cache_accounting_survive_end_payload(self):
        result = _result("")
        result.usage = UsageInfo(prompt_tokens=5, cache_read_input_tokens=7,
                                 prompt_includes_cache=False, cache_observable=False)
        with tool_request("group_minecraft:bench", message_id="mc-17") as origin:
            payload = await _capture(result)
        assert payload["request"]["request_id"] == origin.request_id
        assert payload["request"]["message_id"] == "mc-17"
        assert payload["usage"]["total_input_tokens"] == 12
        assert payload["usage"]["cache_observable"] is False
        assert payload["tool_count"] == 0
        assert payload["message_count"] > 0

    async def test_full_reasoning_included_when_short(self):
        payload = await _capture(_result("短思考"))
        assert payload["reasoning_content"] == "短思考"
        assert payload["reasoning_truncated"] is False
        assert payload["reasoning_preview"] == "短思考"
        assert payload["has_reasoning"] is True

    async def test_long_reasoning_bounded(self):
        payload = await _capture(_result("想" * (_REASONING_TRACE_MAX + 100)))
        assert len(payload["reasoning_content"]) == _REASONING_TRACE_MAX
        assert payload["reasoning_truncated"] is True
        assert len(payload["reasoning_preview"]) == 800

    async def test_no_reasoning_is_none(self):
        payload = await _capture(_result(""))
        assert payload["reasoning_content"] is None
        assert payload["reasoning_truncated"] is False
        assert payload["has_reasoning"] is False
