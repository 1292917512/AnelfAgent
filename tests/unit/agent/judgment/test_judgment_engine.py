"""judgment 引擎测试：通道选择与降级语义。"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from agent.judgment import engine as engine_mod
from agent.judgment.engine import JudgmentConfig, JudgmentEngine
from agent.judgment.types import (
    JudgmentError,
    JudgmentReport,
    JudgmentSource,
    NoulAnswer,
    parse_questions,
)
from core.tool_errors import ErrorCause


def _cfg(**overrides: Any) -> JudgmentConfig:
    base: Dict[str, Any] = dict(
        enabled=True,
        api_key="",
        base_url="https://api.typesafe.ai",
        model="jev-latest",
        timeout=30.0,
        fallback_enabled=True,
        fallback_model="",
        fallback_effort="low",
    )
    base.update(overrides)
    return JudgmentConfig(**base)


def _questions() -> Dict[str, Any]:
    return parse_questions([{"id": "q", "type": "noul", "instructions": "成立吗？"}])


def _report(source: JudgmentSource) -> JudgmentReport:
    return JudgmentReport(answers={"q": NoulAnswer(noul=0.9)}, source=source, model="m")


@pytest.fixture
def engine() -> JudgmentEngine:
    return JudgmentEngine()


class TestChannelResolution:
    def test_disabled(self) -> None:
        assert _cfg(enabled=False).channel == "disabled"

    def test_native_when_key(self) -> None:
        assert _cfg(api_key="k").channel == "typesafe"

    def test_fallback_without_key(self) -> None:
        assert _cfg().channel == "llm_fallback"

    def test_unavailable_without_anything(self) -> None:
        assert _cfg(fallback_enabled=False).channel == "unavailable"


class TestJudge:
    async def test_disabled_rejected(self, engine: JudgmentEngine, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(engine_mod, "load_config", lambda: _cfg(enabled=False))
        with pytest.raises(JudgmentError) as exc_info:
            await engine.judge("state", _questions())
        assert exc_info.value.cause == ErrorCause.STATE

    async def test_empty_questions_rejected(self, engine: JudgmentEngine, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(engine_mod, "load_config", lambda: _cfg())
        with pytest.raises(JudgmentError) as exc_info:
            await engine.judge("state", {})
        assert exc_info.value.cause == ErrorCause.PARAM

    async def test_no_key_goes_fallback(self, engine: JudgmentEngine, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(engine_mod, "load_config", lambda: _cfg())
        called: Dict[str, Any] = {}

        async def fake_native(*args: Any, **kwargs: Any) -> JudgmentReport:
            raise AssertionError("无密钥不应走原生通道")

        async def fake_fallback(self: Any, cfg: Any, state: Any, questions: Any) -> JudgmentReport:
            called["hit"] = True
            return _report(JudgmentSource.LLM_FALLBACK)

        monkeypatch.setattr(JudgmentEngine, "_judge_native", fake_native)
        monkeypatch.setattr(JudgmentEngine, "_judge_fallback", fake_fallback)
        report = await engine.judge("state", _questions())
        assert report.source == JudgmentSource.LLM_FALLBACK and called["hit"]

    async def test_native_success(self, engine: JudgmentEngine, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(engine_mod, "load_config", lambda: _cfg(api_key="k"))

        async def fake_native(self: Any, cfg: Any, state: Any, questions: Any) -> JudgmentReport:
            return _report(JudgmentSource.TYPESAFE)

        monkeypatch.setattr(JudgmentEngine, "_judge_native", fake_native)
        report = await engine.judge("state", _questions())
        assert report.source == JudgmentSource.TYPESAFE

    async def test_native_failure_degrades(self, engine: JudgmentEngine, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(engine_mod, "load_config", lambda: _cfg(api_key="k"))

        async def fake_native(self: Any, cfg: Any, state: Any, questions: Any) -> JudgmentReport:
            raise JudgmentError("网络抖动", cause=ErrorCause.NETWORK, retryable=True)

        async def fake_fallback(self: Any, cfg: Any, state: Any, questions: Any) -> JudgmentReport:
            return _report(JudgmentSource.LLM_FALLBACK)

        monkeypatch.setattr(JudgmentEngine, "_judge_native", fake_native)
        monkeypatch.setattr(JudgmentEngine, "_judge_fallback", fake_fallback)
        report = await engine.judge("state", _questions())
        assert report.source == JudgmentSource.LLM_FALLBACK

    async def test_native_failure_no_fallback_reraise(
        self, engine: JudgmentEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            engine_mod, "load_config", lambda: _cfg(api_key="k", fallback_enabled=False)
        )

        async def fake_native(self: Any, cfg: Any, state: Any, questions: Any) -> JudgmentReport:
            raise JudgmentError("凭据无效", cause=ErrorCause.CONFIG, retryable=False)

        monkeypatch.setattr(JudgmentEngine, "_judge_native", fake_native)
        with pytest.raises(JudgmentError) as exc_info:
            await engine.judge("state", _questions())
        assert exc_info.value.cause == ErrorCause.CONFIG

    async def test_no_key_no_fallback_config_error(
        self, engine: JudgmentEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(engine_mod, "load_config", lambda: _cfg(fallback_enabled=False))
        with pytest.raises(JudgmentError) as exc_info:
            await engine.judge("state", _questions())
        assert exc_info.value.cause == ErrorCause.CONFIG
