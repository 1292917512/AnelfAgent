"""judgment 引擎测试：通道选择与降级语义。"""

from __future__ import annotations

from typing import Any, Dict

import httpx
import pytest

from agent.judgment import client as client_mod
from agent.judgment import engine as engine_mod
from agent.judgment.client import SystemOneClient
from agent.judgment.engine import JudgmentConfig, JudgmentEngine
from agent.judgment.types import (
    JudgmentError,
    JudgmentReport,
    JudgmentSource,
    NoulAnswer,
    parse_questions,
)
from core.tool_errors import ErrorCause


@pytest.mark.parametrize("body", [[], "bad gateway", {"error": "busy"}])
def test_non_object_http_error_keeps_structured_failure(body):
    error = SystemOneClient._http_error(httpx.Response(503, json=body))
    assert error.cause == ErrorCause.NETWORK and error.retryable


@pytest.mark.parametrize("usage", [[1], {"input_tokens": "broken"}])
def test_invalid_usage_is_a_judgment_error(usage):
    with pytest.raises(JudgmentError):
        SystemOneClient._parse_response({"answers": {"q": {"type": "noul", "noul": 0.5}}, "usage": usage}, _questions())


def _cfg(**overrides: Any) -> JudgmentConfig:
    base: Dict[str, Any] = dict(
        enabled=True,
        api_key="",
        base_url="https://api.typesafe.ai" if overrides.get("api_key") else "",
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

    def test_address_alone_enables_local_service(self) -> None:
        cfg = _cfg(base_url="http://127.0.0.1:8090")
        assert cfg.native_configured and cfg.channel == "typesafe"
        assert cfg.base_url == "http://127.0.0.1:8090"



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


@pytest.mark.parametrize("address,path", [
    ("http://127.0.0.1:8090", "/v1/systemone"),
    ("http://localhost:8090/v1/", "/v1/systemone"),
    ("http://[::1]:8090/v1/systemone/", "/v1/systemone"),
    ("https://example.test/decision/v1", "/decision/v1/systemone"),
    ("https://example.test/decision/", "/decision/v1/systemone"),
])
@pytest.mark.parametrize("key", ["", "provider-key"])
async def test_cloud_and_local_use_same_protocol(monkeypatch, address, path, key):
    """真实引擎和 HTTPX 编码通过同一客户端发送三题型，不生成伪造认证头。"""
    import json

    cfg = _cfg(base_url=address, api_key=key, model="StartLux-Decision-27B")
    monkeypatch.setattr(engine_mod, "load_config", lambda: cfg)
    questions = parse_questions([
        {"id": "q", "type": "noul", "instructions": "成立吗？"},
        {"id": "team", "type": "choice", "instructions": "分类", "criteria": {"a": "A", "b": "B"}},
        {"id": "severity", "type": "score", "instructions": "等级", "criteria": ["低", "高"]},
    ])
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST" and request.url.path == path
        assert request.headers.get("Authorization") == (f"Bearer {key}" if key else None)
        payload = json.loads(request.content)
        assert payload["model"] == cfg.model and payload["state"] == {"text": "事实"}
        assert set(payload["questions"]) == {"q", "team", "severity"}
        return httpx.Response(200, json={"answers": {
            "q": {"type": "noul", "noul": 0.95},
            "team": {"type": "choice", "choice": "a", "confidence": 0.8, "probabilities": {"a": 0.9, "b": 0.1}},
            "severity": {"type": "score", "score": 0.8, "confidence": 0.6,
                         "legend": {"0": "低", "1": "高"}, "probabilities": {"0": 0.2, "1": 0.8}},
        }, "model": cfg.model, "usage": {"input_tokens": 120, "output_tokens": 0}})

    monkeypatch.setattr(client_mod, "async_network_client",
                        lambda proxy_mode, **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs))
    report = await JudgmentEngine().judge({"text": "事实"}, questions, native_only=True)
    assert report.source == JudgmentSource.TYPESAFE and report.model == cfg.model
    assert len(report.answers) == 3 and report.usage.output_tokens == 0 and len(requests) == 1


@pytest.mark.parametrize("address", ["localhost:8090", "file:///model.gguf", "https://u:p@host", "http://host?q=key", "http://host#hash", "http://host:invalid"])
def test_invalid_service_address_is_a_configuration_error(address):
    with pytest.raises(JudgmentError) as caught:
        SystemOneClient(base_url=address, api_key="", timeout=2)
    assert caught.value.cause == ErrorCause.CONFIG and not caught.value.retryable


async def test_no_key_no_fallback_config_error(
    engine: JudgmentEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_mod, "load_config", lambda: _cfg(fallback_enabled=False))
    with pytest.raises(JudgmentError) as exc_info:
        await engine.judge("state", _questions())
    assert exc_info.value.cause == ErrorCause.CONFIG
