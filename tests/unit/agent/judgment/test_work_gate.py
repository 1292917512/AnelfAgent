"""Jev 可选工作预判：只接受明确否定，不以失败抑制原流程。"""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from agent.judgment import engine as engine_module
from agent.judgment import gate
from agent.judgment.engine import JudgmentConfig, JudgmentEngine
from agent.judgment.types import JudgmentError, JudgmentReport, JudgmentSource, NoulAnswer
from core.hooks import HookMode, hook_execution
from core.tool_errors import ErrorCause


@pytest.fixture
def engine(monkeypatch):
    config = JudgmentConfig(True, "key", "https://judge.test", "jev", 30, True, "", "low")
    monkeypatch.setattr(gate, "load_config", lambda: config)
    monkeypatch.setattr(engine_module, "load_config", lambda: config)
    result = JudgmentEngine()
    monkeypatch.setattr(gate, "get_judgment_engine", lambda: result)
    fallback = AsyncMock(side_effect=AssertionError("optional work must never use LLM fallback"))
    monkeypatch.setattr(result, "_judge_fallback", fallback)
    return result


@pytest.mark.parametrize("enabled,key", [(True, ""), (False, "configured")])
async def test_unconfigured_runs_without_model_call(engine, monkeypatch, enabled, key):
    config = replace(gate.load_config(), enabled=enabled, api_key=key)
    monkeypatch.setattr(gate, "load_config", lambda: config)
    native = AsyncMock()
    monkeypatch.setattr(engine, "_judge_native", native)
    assert (await gate.assess_optional_work("work", "needed?", {})).run
    native.assert_not_awaited()


@pytest.mark.parametrize("probability,run", [(0, False), (0.1, False), (0.11, True), (0.5, True), (1, True), (-1, True), (2, True), (float("nan"), True)])
async def test_only_confident_valid_negative_skips(engine, monkeypatch, probability, run):
    native = AsyncMock(return_value=JudgmentReport(answers={"needed": NoulAnswer(noul=probability)}, source=JudgmentSource.TYPESAFE))
    monkeypatch.setattr(engine, "_judge_native", native)
    async with hook_execution("gate-test", "test", HookMode.ASYNC) as record:
        decision = await gate.assess_optional_work("work", "needed?", {"scope": "user_qq:42"})
        assert decision.run is run
        assert record.model_calls == 1 and "Jev" in record.detail
    assert native.await_args.args[0].timeout == 2
    engine._judge_fallback.assert_not_awaited()


@pytest.mark.parametrize("failure", [JudgmentError("offline", cause=ErrorCause.NETWORK, retryable=True), ValueError("bad response")])
async def test_failure_keeps_work_without_fallback(engine, monkeypatch, failure):
    monkeypatch.setattr(engine, "_judge_native", AsyncMock(side_effect=failure))
    assert (await gate.assess_optional_work("work", "needed?", {})).run
    engine._judge_fallback.assert_not_awaited()


async def test_timeout_and_external_cancellation(engine, monkeypatch):
    entered = asyncio.Event()
    cancelled = []

    async def pending(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(engine, "_judge_native", pending)
    monkeypatch.setattr(gate, "get_config_float", lambda key, default: 0.1 if key.endswith("timeout") else default)
    assert (await gate.assess_optional_work("work", "needed?", {})).run
    assert cancelled == [True]
    entered.clear()
    task = asyncio.create_task(gate.assess_optional_work("work", "needed?", {}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == [True, True]


async def test_evidence_is_bounded_and_sanitized_without_changing_source(engine, monkeypatch):
    native = AsyncMock(return_value=JudgmentReport(answers={}, source=JudgmentSource.TYPESAFE))
    monkeypatch.setattr(engine, "_judge_native", native)
    state = {"password": "very-secret-value", "evidence": "x" * 20000, "scope": "user_qq:42"}
    assert (await gate.assess_optional_work("work", "needed?", state)).run
    sent = native.await_args.args[1]
    assert sent["truncated"] and len(sent["evidence"]) <= 12000
    assert "very-secret-value" not in str(sent)
    assert state["password"] == "very-secret-value"


def test_context_excludes_prefix_and_keeps_identity_tags():
    messages = [{"role": "system", "content": "stable-prefix"}, {"role": "user", "content": "[uid:42][channel:qq] 过去的安排"}]
    messages.extend({"role": "tool", "content": f"result {index}"} for index in range(12))
    result = gate.conversation_evidence(messages)
    assert "stable-prefix" not in str(result) and "[uid:42]" in str(result)
    assert len(result) == 6 and "result 11" in str(result)
