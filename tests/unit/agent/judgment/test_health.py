"""离线判断零重复等待、后台恢复、配置失效与生命周期验证。"""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from agent.judgment import engine as engine_module
from agent.judgment.catalog import update_catalog
from agent.judgment.diagnostics import test_connection as probe_connection
from agent.judgment.engine import get_judgment_engine, load_config
from agent.judgment.gate import assess_optional_work
from agent.judgment.health import judgment_health
from agent.judgment.types import JudgmentError, JudgmentReport, JudgmentSource, NoulAnswer, NoulQuestion
from core.config import ConfigManager
from core.tool_errors import ErrorCause

QUESTIONS = {"ready": NoulQuestion(instructions="Is this a connection check?")}
REPORT = JudgmentReport(answers={"ready": NoulAnswer(noul=1)}, source=JudgmentSource.TYPESAFE)


@pytest.fixture
def engine(monkeypatch):
    update_catalog("save_provider", "pc", {"name": "PC", "base_url": "http://localhost:8090",
        "models": {"local": {"model": "StartLux-Decision-27B"}, "other": {"model": "another"}}})
    update_catalog("set_default", "local")
    instance = get_judgment_engine()
    monkeypatch.setattr(instance, "_judge_fallback", AsyncMock(side_effect=AssertionError("no LLM fallback expected")))
    return instance


@pytest.mark.parametrize("cause", [ErrorCause.NETWORK, ErrorCause.TIMEOUT, ErrorCause.CONFIG, ErrorCause.INTERNAL])
async def test_failed_optional_model_is_not_retried_in_foreground(engine, monkeypatch, cause):
    native = AsyncMock(side_effect=JudgmentError("offline", cause=cause, retryable=True))
    monkeypatch.setattr(engine, "_judge_native", native)
    assert (await assess_optional_work("recall", "needed?", {})).run
    for _ in range(5):
        assert (await assess_optional_work("recall", "needed?", {})).run
    native.assert_awaited_once()
    status = judgment_health.snapshot()["local"]
    assert status["status"] == "offline" and status["retry_at"] >= time.time() + 299
    assert "other" not in judgment_health.snapshot()


async def test_unknown_model_has_only_one_inflight_probe(engine, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def native(*args):
        entered.set()
        await release.wait()
        return REPORT

    call = AsyncMock(side_effect=native)
    monkeypatch.setattr(engine, "_judge_native", call)
    task = asyncio.create_task(engine.judge("facts", QUESTIONS, native_only=True))
    await asyncio.wait_for(entered.wait(), 1)
    assert (await assess_optional_work("work", "needed?", {})).run
    call.assert_awaited_once()
    release.set()
    await task
    assert judgment_health.snapshot()["local"]["status"] == "ready"


async def test_manual_probe_recovers_without_changing_default(engine, monkeypatch):
    native = AsyncMock(side_effect=JudgmentError("offline", cause=ErrorCause.NETWORK, retryable=True))
    monkeypatch.setattr(engine, "_judge_native", native)
    assert not (await probe_connection("other"))["ok"]
    assert judgment_health.snapshot()["other"]["status"] == "offline"
    native.side_effect, native.return_value = None, REPORT
    assert (await probe_connection("other"))["ok"]
    assert judgment_health.snapshot()["other"]["status"] == "ready"
    assert load_config().model_id == "local"


async def test_background_recheck_waits_for_due_time_and_recovers(engine, monkeypatch):
    failed = AsyncMock(side_effect=JudgmentError("offline", cause=ErrorCause.NETWORK, retryable=True))
    monkeypatch.setattr(engine, "_judge_native", failed)
    await assess_optional_work("work", "needed?", {})
    judgment_health.start()
    await asyncio.sleep(0)
    failed.assert_awaited_once()
    recovered = asyncio.Event()

    async def ready(*args):
        recovered.set()
        return REPORT

    monkeypatch.setattr(engine, "_judge_native", ready)
    judgment_health._states["local"].retry_at = time.time() - 1
    judgment_health._changed.set()
    await asyncio.wait_for(recovered.wait(), 1)
    assert judgment_health.snapshot()["local"]["status"] == "ready"
    await judgment_health.close()
    assert judgment_health._runner is None


async def test_background_shutdown_cancels_pending_request(engine, monkeypatch):
    cfg = load_config()
    judgment_health.begin(cfg, probe=False)
    judgment_health.failure(cfg, JudgmentError("offline", cause=ErrorCause.NETWORK, retryable=True), 0)
    judgment_health._states["local"].retry_at = 0
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def pending(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(engine, "_judge_native", pending)
    judgment_health.start()
    await asyncio.wait_for(entered.wait(), 1)
    await asyncio.wait_for(judgment_health.close(), 1)
    assert cancelled.is_set() and not judgment_health.snapshot()


async def test_config_change_drops_offline_state_and_old_inflight_result(engine, monkeypatch):
    cfg = load_config()
    judgment_health.begin(cfg, probe=False)
    judgment_health.failure(cfg, JudgmentError("offline", cause=ErrorCause.NETWORK, retryable=True), 1)
    update_catalog("save_provider", "pc", {"base_url": "http://localhost:8091"})
    assert not judgment_health.snapshot()
    newer = load_config()
    judgment_health.begin(newer, probe=False)
    judgment_health.success(cfg, 1)
    assert judgment_health.snapshot()["local"]["status"] == "checking"
    judgment_health.success(newer, 0.2)
    assert judgment_health.snapshot()["local"]["status"] == "ready"


async def test_background_failures_reschedule_and_other_models_still_work(engine, monkeypatch):
    native = AsyncMock(side_effect=JudgmentError("offline", cause=ErrorCause.NETWORK, retryable=True))
    monkeypatch.setattr(engine, "_judge_native", native)
    await assess_optional_work("work", "needed?", {})
    state = judgment_health._states["local"]
    state.retry_at = 0
    await judgment_health._recheck("local", state)
    assert state.retry_at >= time.time() + 299 and state.status == "offline"
    native.side_effect, native.return_value = None, REPORT
    await engine.judge("facts", QUESTIONS, model_id="other", native_only=True)
    assert judgment_health.snapshot()["local"]["status"] == "offline"
    assert judgment_health.snapshot()["other"]["status"] == "ready"


async def test_timeout_respects_budget_and_external_cancel_is_not_offline(engine, monkeypatch):
    cfg = replace(load_config(), timeout=0.02)
    monkeypatch.setattr(engine_module, "load_config", lambda: cfg)
    pending = AsyncMock(side_effect=lambda *args: None)

    async def wait(*args):
        await asyncio.Event().wait()

    pending.side_effect = wait
    monkeypatch.setattr(engine, "_judge_native", pending)
    with pytest.raises(JudgmentError) as caught:
        await engine.judge("facts", QUESTIONS, native_only=True)
    assert caught.value.cause == ErrorCause.TIMEOUT
    assert judgment_health.snapshot()["local"]["status"] == "offline"
    judgment_health.invalidate({"local"})
    task = asyncio.create_task(engine.judge("facts", QUESTIONS, native_only=True))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert judgment_health.snapshot()["local"]["status"] == "unknown"


async def test_disabled_model_and_parameter_failures_do_not_trigger_background_loop(engine, monkeypatch):
    cfg = load_config()
    judgment_health.begin(cfg, probe=False)
    judgment_health.failure(cfg, JudgmentError("bad question", cause=ErrorCause.PARAM, retryable=False), 0)
    assert judgment_health.snapshot()["local"]["status"] == "unknown"
    judgment_health.failure(cfg, JudgmentError("offline", cause=ErrorCause.NETWORK, retryable=True), 0)
    state = judgment_health._states["local"]
    state.retry_at = 0
    ConfigManager.set("judgment_enabled", False)
    native = AsyncMock()
    monkeypatch.setattr(engine, "_judge_native", native)
    await judgment_health._recheck("local", state)
    assert state.retry_at > time.time()
    native.assert_not_awaited()
