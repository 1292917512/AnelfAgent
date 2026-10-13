"""作业汇报 Jev 门卫：例行跳过、异常放行、失败保留原流程与 worker 接线。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from agent.judgment.engine import JudgmentConfig
from agent.judgment.types import (
    JudgmentError,
    JudgmentReport,
    JudgmentSource,
    NoulAnswer,
)
from channels.minecraft.judgment_gate import verdict_needed
from channels.minecraft.worker import MinecraftWorker
from core.tool_errors import ErrorCause


def _config(*, native: bool = True, enabled: bool = True) -> JudgmentConfig:
    return JudgmentConfig(
        enabled=enabled,
        api_key="key" if native else "",
        base_url="https://judgment.example" if native else "",
        model="jev",
        timeout=5.0,
        fallback_enabled=True,
        fallback_model="",
        fallback_effort="low",
    )


class FakeEngine:
    """最小判断引擎：记录调用，按预设答案或异常回放。"""

    def __init__(
        self,
        answers: dict[str, NoulAnswer] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.answers = answers or {}
        self.error = error
        self.calls: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []

    async def judge(
        self, state: Any, questions: dict[str, Any], **kwargs: Any
    ) -> JudgmentReport:
        self.calls.append((state, questions, kwargs))
        if self.error is not None:
            raise self.error
        return JudgmentReport(
            answers=dict(self.answers), source=JudgmentSource.TYPESAFE, model="jev"
        )


def _noul(value: float) -> NoulAnswer:
    return NoulAnswer(noul=value)


# ---------------------------------------------------------------------------
# verdict_needed：门卫判定逻辑
# ---------------------------------------------------------------------------


async def test_routine_report_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(answers={"intervene": _noul(0.0), "announce": _noul(0.05)})
    monkeypatch.setattr("channels.minecraft.judgment_gate.load_config", lambda: _config())
    monkeypatch.setattr("channels.minecraft.judgment_gate.get_judgment_engine", lambda: engine)

    assert await verdict_needed("自主作业第 3 组泥土完成：新入包 64/64。") is False
    assert len(engine.calls) == 1
    assert engine.calls[0][2]["native_only"] is True


async def test_intervention_report_runs_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(answers={"intervene": _noul(0.9), "announce": _noul(0.0)})
    monkeypatch.setattr("channels.minecraft.judgment_gate.load_config", lambda: _config())
    monkeypatch.setattr("channels.minecraft.judgment_gate.get_judgment_engine", lambda: engine)

    assert await verdict_needed("自主作业第 4 组受阻：GATHER_NO_ROUTE。", blocked_reason="GATHER_NO_ROUTE") is True


async def test_announcement_only_report_runs_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(answers={"intervene": _noul(0.0), "announce": _noul(0.9)})
    monkeypatch.setattr("channels.minecraft.judgment_gate.load_config", lambda: _config())
    monkeypatch.setattr("channels.minecraft.judgment_gate.get_judgment_engine", lambda: engine)

    assert await verdict_needed("自主作业累计 1000 个，值得说一声。") is True


async def test_unconfigured_service_fails_open_without_calling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(
        "channels.minecraft.judgment_gate.load_config", lambda: _config(native=False)
    )
    monkeypatch.setattr("channels.minecraft.judgment_gate.get_judgment_engine", lambda: engine)

    assert await verdict_needed("自主作业第 1 组泥土完成。") is True
    assert engine.calls == []


async def test_disabled_judgment_fails_open_without_calling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    monkeypatch.setattr(
        "channels.minecraft.judgment_gate.load_config", lambda: _config(enabled=False)
    )
    monkeypatch.setattr("channels.minecraft.judgment_gate.get_judgment_engine", lambda: engine)

    assert await verdict_needed("自主作业第 1 组泥土完成。") is True
    assert engine.calls == []


async def test_judgment_error_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(
        error=JudgmentError("判断服务请求超时", cause=ErrorCause.TIMEOUT, retryable=True)
    )
    monkeypatch.setattr("channels.minecraft.judgment_gate.load_config", lambda: _config())
    monkeypatch.setattr("channels.minecraft.judgment_gate.get_judgment_engine", lambda: engine)

    assert await verdict_needed("自主作业第 2 组泥土完成。") is True


async def test_missing_answers_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = FakeEngine(answers={"intervene": _noul(0.0)})
    monkeypatch.setattr("channels.minecraft.judgment_gate.load_config", lambda: _config())
    monkeypatch.setattr("channels.minecraft.judgment_gate.get_judgment_engine", lambda: engine)

    assert await verdict_needed("自主作业第 2 组泥土完成。") is True


async def test_oversized_report_fails_open_without_calling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    monkeypatch.setattr("channels.minecraft.judgment_gate.load_config", lambda: _config())
    monkeypatch.setattr("channels.minecraft.judgment_gate.get_judgment_engine", lambda: engine)

    assert await verdict_needed("很" * 3000) is True
    assert engine.calls == []


# ---------------------------------------------------------------------------
# worker 接线：门卫跳过裁决轮 / 关闭门卫保留原流程
# ---------------------------------------------------------------------------


class _FakeCall:
    async def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"active": False}


class _Mind:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def reflect(self, messages: list[dict[str, str]], **_kwargs: Any) -> str:
        self.prompts.append(messages[0]["content"])
        return "WORK_VERDICT=continue\n泥土挖好一组，继续挖。"


class _Runtime:
    def __init__(self, mind: _Mind) -> None:
        self.mind = mind


def _worker(judgment_gate: Callable[[], bool] | None) -> tuple[MinecraftWorker, _Mind, list[str]]:
    mind = _Mind()
    announces: list[str] = []

    async def record(text: str) -> None:
        return None

    async def announce(text: str) -> None:
        announces.append(text)

    worker = MinecraftWorker(
        _FakeCall(),
        server_id=lambda: "local",
        online=lambda: True,
        enabled=lambda: True,
        item=lambda: "dirt",
        stack_size=lambda: 64,
        poll_seconds=lambda: 0.01,
        record=record,
        announce=announce,
        judgment_gate=judgment_gate,
    )
    return worker, mind, announces


async def test_worker_gate_skips_reflect_for_routine_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _false(report_line: str, *, blocked_reason: str = "") -> bool:
        return False

    monkeypatch.setattr("channels.minecraft.worker.verdict_needed", _false)
    worker, mind, announces = _worker(judgment_gate=lambda: True)

    await worker._verdict_turn("自主作业第 3 组泥土完成：新入包 64/64。")

    assert mind.prompts == []
    assert announces == []


async def test_worker_gate_disabled_keeps_reflect(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _must_not_call(report_line: str, *, blocked_reason: str = "") -> bool:
        raise AssertionError("门卫关闭时不应调用 Jev 预判")

    monkeypatch.setattr("channels.minecraft.worker.verdict_needed", _must_not_call)
    worker, mind, announces = _worker(judgment_gate=lambda: False)
    monkeypatch.setattr("agent.runtime.singleton.get_runtime", lambda: _Runtime(mind))

    await worker._verdict_turn("自主作业第 3 组泥土完成：新入包 64/64。")

    assert len(mind.prompts) == 1
    assert announces == ["泥土挖好一组，继续挖。"]
