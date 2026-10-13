"""判断请求与零推理连通检查共用代理规则及失败回退。"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from agent.judgment.client import SystemOneClient
from agent.judgment.engine import JudgmentConfig
from agent.judgment.health import JudgmentHealth
from agent.judgment.types import JudgmentError, NoulQuestion
from core.config import ConfigManager
from core.tool_errors import ErrorCause


@pytest.fixture
def network_settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    values: dict[str, Any] = {
        "proxy_enabled": True, "http_proxy": "http://proxy.test:7890",
        "proxy_default_mode": "fallback", "judgment_connect_timeout": 0.2,
    }
    monkeypatch.setattr(ConfigManager, "get", lambda key, default=None: values.get(key, default))
    return values


def config(url: str = "https://api.typesafe.ai") -> JudgmentConfig:
    return JudgmentConfig(enabled=True, api_key="test-fixture", base_url=url, model="jev-latest", timeout=5,
                          fallback_enabled=False, fallback_model="", fallback_effort="low", model_id="cloud")


@pytest.mark.parametrize("failure", ["connect", "read"])
async def test_connectivity_falls_back_before_health_deadline(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    calls: list[str | None] = []

    def transport(*, proxy: str | None = None, **kwargs: Any) -> httpx.MockTransport:
        async def respond(request: httpx.Request) -> httpx.Response:
            calls.append(proxy)
            assert request.method == "HEAD" and not request.content
            assert "authorization" not in request.headers
            if proxy is None:
                await asyncio.sleep(request.extensions["timeout"][failure])
                error = httpx.ConnectTimeout if failure == "connect" else httpx.ReadTimeout
                raise error("Direct route timed out", request=request)
            return httpx.Response(405)
        return httpx.MockTransport(respond)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    health = JudgmentHealth()
    await health.check_connectivity(config())
    assert calls == [None, network_settings["http_proxy"]]
    assert health.snapshot()["cloud"]["status"] == "ready"
    assert health.snapshot()["cloud"]["check_kind"] == "connectivity"


@pytest.mark.parametrize(("url", "expected_proxy"), [
    ("https://api.typesafe.ai", "http://proxy.test:7890"),
    ("http://192.168.1.4:10095", None),
])
async def test_probe_and_inference_share_rules_without_charging_probe(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch, url: str, expected_proxy: str | None,
) -> None:
    network_settings["proxy_rules"] = "DOMAIN,api.typesafe.ai,PROXY"
    requests: list[tuple[str | None, httpx.Request]] = []

    def transport(*, proxy: str | None = None, **kwargs: Any) -> httpx.MockTransport:
        def respond(request: httpx.Request) -> httpx.Response:
            requests.append((proxy, request))
            return httpx.Response(405) if request.method == "HEAD" else httpx.Response(200, json={
                "answers": {"q": {"type": "noul", "noul": 0.9}}, "model": "jev-latest",
            })
        return httpx.MockTransport(respond)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    cfg = config(url)
    await JudgmentHealth().check_connectivity(cfg)
    await SystemOneClient(base_url=url, api_key=cfg.api_key, timeout=cfg.timeout).evaluate(
        state="Explicit inference test", model=cfg.model, questions={"q": NoulQuestion(instructions="Ready?")},
    )
    assert [(proxy, request.method) for proxy, request in requests] == [(expected_proxy, "HEAD"), (expected_proxy, "POST")]
    assert not requests[0][1].content and "authorization" not in requests[0][1].headers
    assert requests[1][1].headers["authorization"] == "Bearer test-fixture"


async def test_proxy_auth_failure_does_not_mark_the_model_reachable(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_settings["proxy_default_mode"] = "proxy"
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(lambda request: httpx.Response(407)))
    health = JudgmentHealth()
    with pytest.raises(JudgmentError) as error:
        await health.check_connectivity(config())
    assert error.value.cause == ErrorCause.CONFIG
    state = health.snapshot()["cloud"]
    assert state["status"] == "offline" and state["retry_at"] == 0
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(lambda request: httpx.Response(405)))
    await health.check_connectivity(config())
    assert health.snapshot()["cloud"]["status"] == "ready"


async def test_probe_cancellation_releases_connection_without_marking_offline(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, released = asyncio.Event(), asyncio.Event()

    async def respond(request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await asyncio.Event().wait()
            raise AssertionError("The request must be cancelled")
        finally:
            released.set()

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond))
    health = JudgmentHealth()
    task = asyncio.create_task(health.check_connectivity(config()))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released.is_set()
    assert health.snapshot()["cloud"]["status"] == "unknown"
