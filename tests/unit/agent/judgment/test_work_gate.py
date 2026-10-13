"""Jev 可选工作预判：只接受明确否定，不以失败抑制原流程。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.hooks_llm.progress import HookProgress
from agent.judgment import engine as engine_module
from agent.judgment import gate
from agent.judgment.engine import JudgmentConfig, JudgmentEngine
from agent.judgment.types import ChoiceAnswer, JudgmentError, JudgmentReport, JudgmentSource, JudgmentUsage, NoulAnswer
from core.hooks import HookMode, hook_execution
from core.tool_errors import ErrorCause


def report(probability=0.05, basis="covered", basis_probability=1):
    probabilities = {key: (1 - basis_probability) / 4 for key in ("uncertain", "missing", "improvement", "covered", "irrelevant")}
    probabilities[basis] = basis_probability
    return JudgmentReport(
        answers={"needed": NoulAnswer(noul=probability),
                 "basis": ChoiceAnswer(choice=basis, probabilities=probabilities, confidence=1)},
        source=JudgmentSource.TYPESAFE, model="jev-test", usage=JudgmentUsage(input_tokens=120, output_tokens=20),
    )


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
    config = replace(gate.load_config(), enabled=enabled, api_key=key, base_url="")
    monkeypatch.setattr(gate, "load_config", lambda: config)
    native = AsyncMock()
    monkeypatch.setattr(engine, "_judge_native", native)
    assert (await gate.assess_optional_work("work", "needed?", {})).run
    native.assert_not_awaited()


async def test_local_service_without_key_can_skip_optional_work(engine, monkeypatch):
    config = replace(gate.load_config(), api_key="", base_url="http://127.0.0.1:8090", model="StartLux-Decision-27B")
    monkeypatch.setattr(gate, "load_config", lambda: config)
    monkeypatch.setattr(engine_module, "load_config", lambda: config)
    native = AsyncMock(return_value=report())
    monkeypatch.setattr(engine, "_judge_native", native)
    async with hook_execution("local-gate", "test", HookMode.ASYNC) as record:
        assert not (await gate.assess_optional_work("work", "needed?", {})).run
        assert record.judgments[0].status == "skip"
    assert native.await_args.args[0].api_key == ""
    engine._judge_fallback.assert_not_awaited()


@pytest.mark.parametrize("probability,run", [(0, False), (0.1, False), (0.11, True), (0.5, True), (1, True), (-1, True), (2, True), (float("nan"), True)])
async def test_only_confident_valid_negative_skips(engine, monkeypatch, probability, run):
    native = AsyncMock(return_value=report(probability))
    monkeypatch.setattr(engine, "_judge_native", native)
    async with hook_execution("gate-test", "test", HookMode.ASYNC) as record:
        decision = await gate.assess_optional_work("work", "needed?", {"scope": "user_qq:42"})
        assert decision.run is run
        assert record.model_calls == 1 and "Jev" in record.detail
        item = record.judgments[0]
        assert item.status == ("skip" if not run else "fallback")
        assert item.input_tokens == 120 and item.output_tokens == 20
    assert native.await_args.args[0].timeout == 2
    engine._judge_fallback.assert_not_awaited()
    assert set(native.await_args.args[2]) == {"needed", "basis"}
    assert native.await_args.args[1]["evidence"]["scope"] == "user_qq:42"
    native.assert_awaited_once()


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
    async with hook_execution("timeout", "test", HookMode.ASYNC) as record:
        assert (await gate.assess_optional_work("work", "needed?", {})).run
        assert record.judgments[0].slow and record.judgments[0].status == "fallback"
    assert cancelled == [True]
    assert (await gate.assess_optional_work("work", "needed?", {})).run
    assert cancelled == [True]
    await gate.judgment_health.close()
    entered.clear()
    async with hook_execution("cancel", "test", HookMode.ASYNC) as record:
        task = asyncio.create_task(gate.assess_optional_work("work", "needed?", {}))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert record.judgments[0].status == "cancelled"
    assert cancelled == [True, True]


async def test_evidence_is_bounded_and_sanitized_without_changing_source(engine, monkeypatch):
    native = AsyncMock(return_value=JudgmentReport(answers={}, source=JudgmentSource.TYPESAFE))
    monkeypatch.setattr(engine, "_judge_native", native)
    state = {"password": "very-secret-value", "evidence": "x" * 1000, "scope": "user_qq:42"}
    assert (await gate.assess_optional_work("work", "needed?", state)).run
    sent = native.await_args.args[1]
    assert not sent["truncated"] and sent["evidence"]["scope"] == "user_qq:42"
    assert "very-secret-value" not in str(sent)
    assert state["password"] == "very-secret-value"


def test_context_excludes_prefix_and_keeps_identity_tags():
    messages = [{"role": "system", "content": "stable-prefix"}, {"role": "user", "content": "[uid:42][channel:qq] 过去的安排"}]
    messages.extend({"role": "tool", "content": f"result {index}"} for index in range(12))
    result = gate.conversation_evidence(messages)
    assert "stable-prefix" not in str(result) and "[uid:42]" in str(result)
    assert len(result) == 6 and "result 11" in str(result)


@pytest.mark.parametrize("probability,basis,confidence,status", [
    (0.01, "missing", 1, "fallback"), (0.99, "covered", 1, "fallback"),
    (0.01, "uncertain", 1, "fallback"), (0.01, "covered", 0.6, "fallback"),
    (0.01, "covered", 0.9, "skip"), (0.99, "improvement", 1, "proceed"),
])
async def test_independent_answers_must_agree(engine, monkeypatch, probability, basis, confidence, status):
    monkeypatch.setattr(engine, "_judge_native", AsyncMock(return_value=report(probability, basis, confidence)))
    async with hook_execution("review", "test", HookMode.ASYNC) as record:
        decision = await gate.assess_optional_work("work", "needed?", {})
        assert record.judgments[0].status == status
        assert decision.run is (status != "skip")


async def test_truncation_never_suppresses_work(engine, monkeypatch):
    native = AsyncMock(return_value=report())
    monkeypatch.setattr(engine, "_judge_native", native)
    async with hook_execution("review", "test", HookMode.ASYNC) as record:
        assert (await gate.assess_optional_work("work", "needed?", {"body": "x" * 13000})).run
        assert record.judgments[0].input_truncated
        native.assert_not_awaited()


async def test_slow_result_remains_visible_to_ai_after_hook_changes(engine, monkeypatch):
    clock = iter([0, 1.2])
    monkeypatch.setattr(gate, "time", SimpleNamespace(time=gate.time.time, monotonic=lambda: next(clock)))
    monkeypatch.setattr(engine, "_judge_native", AsyncMock(return_value=report()))
    async with hook_execution("review", "test", HookMode.ASYNC) as record:
        progress = HookProgress(record)
        await gate.assess_optional_work("work", "needed?", {})
        await progress.update("model", "another operation")
        record.detail = "review completed"
        item = record.judgments[0]
        assert item.duration_ms == 1200 and item.slow
        assert item.status == "skip" and item.model == "jev-test"
        assert progress.snapshot()["judgments"][0]["slow"]
