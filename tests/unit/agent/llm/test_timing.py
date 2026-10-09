"""LLM 调用阶段计时与可观测边界。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent.llm.config import LLMClientConfig
from agent.llm.llm_client import LLMClient
from agent.llm.response_parsing import _iter_stream
from agent.llm.timing import LLMCallTiming, llm_timing_context


def test_timing_snapshot_keeps_monotonic_durations_and_unknown_send_boundary() -> None:
    timing = LLMCallTiming(
        {"request_id": "request-1", "message_id": "mc-1"}, "reply", True,
    )
    with llm_timing_context(timing):
        timing.begin_attempt("chat_completions", True)
        timing.mark_sdk_started()
        timing.mark_raw_chunk()
        timing.mark_model_delta()
        timing.mark_sdk_finished()
        timing.finish()
    snapshot = timing.snapshot()
    attempt = snapshot["attempts"][0]
    assert snapshot["request"]["request_id"] == "request-1"
    assert snapshot["duration_ms"] >= 0
    assert attempt["sdk_start_ms"] is not None
    assert attempt["raw_first_chunk_ms"] is not None
    assert attempt["model_first_delta_ms"] is not None
    assert attempt["request_sent_at_utc"] is None
    assert attempt["request_send_observed"] is False


@pytest.mark.asyncio
async def test_chat_completion_adaptive_retries_are_separate_timing_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LLMClient.__new__(LLMClient)
    import litellm

    client.config = LLMClientConfig(model="m", name="test")
    client._learned_no_forced_tool_choice = False
    client._learned_output_cap = None
    client._learned_dropped_params = set()
    calls = 0

    async def fake_completion(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise litellm.BadRequestError(
                "tool_choice is not supported", model="m", llm_provider="test",
            )
        return SimpleNamespace(id="provider-1")

    monkeypatch.setattr("agent.llm.llm_client.litellm.acompletion", fake_completion)
    timing = LLMCallTiming({}, "reply", False)
    with llm_timing_context(timing):
        response = await client._start_completion({
            "model": "m", "messages": [], "tool_choice": "required",
        })
    assert response.id == "provider-1"
    assert calls == 2
    assert len(timing.attempts) == 2
    assert timing.attempts[0].error is not None
    assert timing.attempts[1].provider_request_id == "provider-1"


@pytest.mark.asyncio
async def test_stream_parser_marks_first_raw_chunk() -> None:
    async def source():
        yield SimpleNamespace(choices=[], usage=None)
        yield SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="ok", reasoning_content=None),
                finish_reason="stop",
            )],
            usage=None,
        )

    timing = LLMCallTiming({}, "reply", True)
    timing.begin_attempt("chat_completions", True)
    timing.mark_sdk_started()
    with llm_timing_context(timing):
        deltas = [delta async for delta in _iter_stream(source(), {}, None)]
    assert any(delta.content == "ok" for delta in deltas)
    assert timing.attempts[0].raw_chunk_mono is not None
