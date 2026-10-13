"""通道测速的原生归因、预算与速度警告。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.judgment import diagnostics as judgment
from agent.judgment.engine import JudgmentConfig
from agent.judgment.types import JudgmentError, JudgmentReport, JudgmentSource
from core.tool_errors import ErrorCause


@pytest.mark.parametrize("elapsed,slow", [(0.2, False), (1.2, True), (2.5, True)])
@pytest.mark.parametrize("key", ["", "configured"])
async def test_speed_and_native_source_are_reported(monkeypatch, elapsed, slow, key):
    cfg = JudgmentConfig(True, key, "http://localhost:8090", "jev", 30, True, "", "low")
    monkeypatch.setattr(judgment, "load_config", lambda: cfg)
    native = AsyncMock(return_value=JudgmentReport(answers={}, source=JudgmentSource.TYPESAFE))
    monkeypatch.setattr(judgment, "get_judgment_engine", lambda: SimpleNamespace(judge=native))
    times = iter([0, elapsed])
    monkeypatch.setattr(judgment, "time", SimpleNamespace(monotonic=lambda: next(times)))
    result = await judgment.test_inference()
    assert result["ok"] and result["slow"] is slow
    assert result["latency_ms"] == elapsed * 1000
    assert result["gate_budget_ms"] == 5000 and result["slow_after_ms"] == 1000
    assert native.await_args.kwargs["native_only"] is True


async def test_native_failure_is_not_disguised_by_fallback(monkeypatch):
    cfg = JudgmentConfig(True, "", "http://localhost:8090", "StartLux-Decision-27B", 30, True, "", "low")
    monkeypatch.setattr(judgment, "load_config", lambda: cfg)
    native = AsyncMock(side_effect=JudgmentError("unavailable", cause=ErrorCause.NETWORK, retryable=True))
    monkeypatch.setattr(judgment, "get_judgment_engine", lambda: SimpleNamespace(judge=native))
    result = await judgment.test_inference()
    assert not result["ok"] and result["cause"] == "network"
    assert "latency_ms" in result
    assert result["source"] == "typesafe" and native.await_args.kwargs["native_only"]



async def test_test_request_has_a_total_deadline_and_propagates_cancellation(monkeypatch):
    cfg = JudgmentConfig(True, "configured", "https://test", "jev", 0.01, True, "", "low")
    monkeypatch.setattr(judgment, "load_config", lambda: cfg)
    cancelled = []

    async def pending(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(judgment, "get_judgment_engine", lambda: SimpleNamespace(judge=pending))
    assert (await judgment.test_inference())["cause"] == "timeout"
    assert cancelled == [True]
    task = asyncio.create_task(judgment.test_inference())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
