"""判断请求与零推理连通检查共用服务商的显式代理选择。"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from agent.judgment.catalog import update_catalog
from agent.judgment.engine import JudgmentConfig, JudgmentEngine, get_judgment_engine
from agent.judgment.health import JudgmentHealth, judgment_health
from agent.judgment.types import JudgmentError, NoulQuestion
from core.config import ConfigManager
from core.tool_errors import ErrorCause


@pytest.fixture
def network_settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    values: dict[str, Any] = {
        "proxy_enabled": True, "http_proxy": "http://proxy.test:7890",
        "proxy_default_mode": "fallback", "judgment_connect_timeout": 0.2,
        "judgment_probe_timeout": 0.5,
    }
    monkeypatch.setattr(ConfigManager, "get", lambda key, default=None: values.get(key, default))
    return values


def config(url: str = "https://api.typesafe.ai", *, use_proxy: bool = False) -> JudgmentConfig:
    return JudgmentConfig(enabled=True, api_key="test-fixture", base_url=url, model="jev-latest", timeout=5,
                          fallback_enabled=False, fallback_model="", fallback_effort="low", model_id="cloud", use_proxy=use_proxy)


@pytest.mark.parametrize("use_proxy", [False, True])
@pytest.mark.parametrize("url", ["https://api.typesafe.ai", "http://192.168.1.4:10095"])
async def test_probe_and_inference_share_explicit_route_without_charging_probe(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch, url: str, use_proxy: bool,
) -> None:
    network_settings["proxy_default_mode"] = "direct" if use_proxy else "proxy"
    network_settings["proxy_rules"] = "DOMAIN,api.typesafe.ai,PROXY\nIP-CIDR,192.168.0.0/16,PROXY"
    monkeypatch.setenv("HTTPS_PROXY", "http://environment-proxy.test:7890")
    monkeypatch.setenv("HTTP_PROXY", "http://environment-proxy.test:7890")
    requests: list[tuple[str | None, httpx.Request]] = []

    def transport(*, proxy: str | None = None, **kwargs: Any) -> httpx.MockTransport:
        def respond(request: httpx.Request) -> httpx.Response:
            requests.append((proxy, request))
            return httpx.Response(405) if request.method == "HEAD" else httpx.Response(200, json={
                "answers": {"q": {"type": "noul", "noul": 0.9}}, "model": "jev-latest",
            })
        return httpx.MockTransport(respond)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    cfg = config(url, use_proxy=use_proxy)
    health = JudgmentHealth()
    await health.check_connectivity(cfg)
    await JudgmentEngine()._judge_native(cfg, "Explicit inference test", {"q": NoulQuestion(instructions="Ready?")})
    expected_proxy = network_settings["http_proxy"] if use_proxy else None
    assert [(proxy, request.method) for proxy, request in requests] == [(expected_proxy, "HEAD"), (expected_proxy, "POST")]
    assert not requests[0][1].content and "authorization" not in requests[0][1].headers
    assert requests[1][1].headers["authorization"] == "Bearer test-fixture"
    assert health.snapshot()["cloud"]["status"] == "ready"
    assert health.snapshot()["cloud"]["check_kind"] == "connectivity"


@pytest.mark.parametrize("probe", [False, True])
async def test_enabled_proxy_requires_global_configuration_without_silent_direct_fallback(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch, probe: bool,
) -> None:
    network_settings["proxy_enabled"] = False

    def unexpected_transport(**kwargs: Any) -> httpx.MockTransport:
        raise AssertionError("No route may be attempted without the configured proxy")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", unexpected_transport)
    cfg = config(use_proxy=True)
    with pytest.raises(JudgmentError) as error:
        if probe:
            await JudgmentHealth().check_connectivity(cfg)
        else:
            await JudgmentEngine()._judge_native(cfg, "facts", {"q": NoulQuestion(instructions="Ready?")})
    assert error.value.cause == ErrorCause.CONFIG and not error.value.retryable
    assert "network/proxy" in str(error.value)


async def test_proxy_auth_failure_does_not_mark_the_model_reachable(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(lambda request: httpx.Response(407)))
    health = JudgmentHealth()
    with pytest.raises(JudgmentError) as error:
        await health.check_connectivity(config(use_proxy=True))
    assert error.value.cause == ErrorCause.CONFIG
    state = health.snapshot()["cloud"]
    assert state["status"] == "offline" and state["retry_at"] == 0
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(lambda request: httpx.Response(405)))
    await health.check_connectivity(config(use_proxy=True))
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


async def test_priority_failover_and_background_recovery_keep_each_providers_route(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    for provider_id, use_proxy in [("local", False), ("cloud", True)]:
        update_catalog("save_provider", provider_id, {"name": provider_id, "base_url": f"http://{provider_id}.test",
            "use_proxy": use_proxy, "models": {provider_id: {"model": provider_id}}})
    update_catalog("set_priority", "", {"model_ids": ["local", "cloud"]})
    offline = {"local.test"}
    calls: list[tuple[str, str | None, str]] = []

    def transport(*, proxy: str | None = None, **kwargs: Any) -> httpx.MockTransport:
        def respond(request: httpx.Request) -> httpx.Response:
            calls.append((request.url.host, proxy, request.method))
            if request.url.host in offline:
                raise httpx.ConnectError("offline", request=request)
            if request.method == "HEAD":
                assert not request.content and "authorization" not in request.headers
                return httpx.Response(405)
            return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 0.9}},
                                            "model": json.loads(request.content)["model"]})
        return httpx.MockTransport(respond)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    engine = get_judgment_engine()
    questions = {"q": NoulQuestion(instructions="Ready?")}
    assert (await engine.judge("facts", questions, native_only=True)).model == "cloud"
    offline.clear()
    await judgment_health._recheck("local", judgment_health._states["local"])
    assert (await engine.judge("facts", questions, native_only=True)).model == "local"
    assert calls == [("local.test", None, "POST"), ("cloud.test", network_settings["http_proxy"], "POST"),
                     ("local.test", None, "HEAD"), ("local.test", None, "POST")]


@pytest.mark.parametrize("probe", [False, True])
async def test_access_restriction_is_not_reported_as_healthy(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch, probe: bool,
) -> None:
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(lambda request: httpx.Response(451)))
    cfg = config()
    health = JudgmentHealth()
    with pytest.raises(JudgmentError, match="HTTP 451") as error:
        if probe:
            await health.check_connectivity(cfg)
        else:
            await JudgmentEngine()._judge_native(cfg, "facts", {"q": NoulQuestion(instructions="Ready?")})
    assert error.value.cause == ErrorCause.NETWORK and not error.value.retryable
    if probe:
        assert health.snapshot()["cloud"]["status"] == "offline"
        assert health.snapshot()["cloud"]["retry_at"] > 0


async def test_head_response_budget_is_independent_of_inference_connect_deadline(
    network_settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    network_settings["judgment_connect_timeout"] = 0.02
    timeouts: dict[str, dict[str, float]] = {}

    async def respond(request: httpx.Request) -> httpx.Response:
        timeouts[request.method] = request.extensions["timeout"]
        if request.method == "HEAD":
            await asyncio.sleep(0.08)
            return httpx.Response(405)
        return httpx.Response(200, json={"answers": {"q": {"type": "noul", "noul": 1}}})

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond))
    cfg = config(use_proxy=True)
    health = JudgmentHealth()
    await health.check_connectivity(cfg)
    await JudgmentEngine()._judge_native(cfg, "facts", {"q": NoulQuestion(instructions="Ready?")})
    assert health.snapshot()["cloud"]["status"] == "ready"
    assert timeouts["HEAD"]["read"] == 0.5
    assert timeouts["POST"]["connect"] == 0.02
    assert timeouts["POST"]["read"] == cfg.timeout
