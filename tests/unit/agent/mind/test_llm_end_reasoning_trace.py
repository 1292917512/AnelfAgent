"""LLM_END 事件的思考全文载荷契约。

思考全文仅供思维链面板展示：有界截断后驻留 tracer 内存会话
（max_sessions 滚动销毁），不落库、不注入 LLM 上下文。
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.llm.llm_client import LLMClient
from agent.llm.types import ChatResult, UsageInfo
from agent.mind.llm_invoker import _REASONING_TRACE_MAX, _invoke_llm_unified
from core.event_bus import EVENT_THINKING_LLM_END, event_bus
from core.tool_context import tool_request


def _result(reasoning: str) -> ChatResult:
    return ChatResult(
        content="答",
        reasoning_content=reasoning,
        model="fake",
        finish_reason="stop",
        usage=UsageInfo(prompt_tokens=5, completion_tokens=2, total_tokens=7),
    )


def _mind_stub(result: ChatResult) -> SimpleNamespace:
    client = object.__new__(LLMClient)
    client.config = SimpleNamespace(model="fake", name="fake", litellm_model="", api_type="")

    async def _chat(messages, tools, **kwargs):
        return result

    return SimpleNamespace(
        llm=client,
        session_llm_params={},
        _llm_chat_with_retry=_chat,
        get_model_context_length=lambda: 128000,
        _get_mind_config=lambda: SimpleNamespace(
            reasoning_effort=None, llm_timeout=30, log_ai_output=False,
        ),
    )


async def _capture(result: ChatResult) -> dict:
    seen: list[dict] = []

    async def _on_end(payload):
        seen.append(payload)

    event_bus.on(EVENT_THINKING_LLM_END, _on_end, owner="test:reasoning-trace")
    try:
        await _invoke_llm_unified(_mind_stub(result), [{"role": "user", "content": "hi"}], None)
    finally:
        event_bus.off_by_owner("test:reasoning-trace")
    assert seen, "LLM_END 事件未发射"
    return seen[-1]


class TestReasoningTracePayload:
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
                await _invoke_llm_unified(_mind_stub(_result("thought")), messages, tools)
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
