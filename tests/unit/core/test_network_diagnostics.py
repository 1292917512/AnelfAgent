"""真实路由传输下的诊断结果、取消、配置变化与凭据边界。"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from pydantic import ValidationError

from core.config import ConfigManager
from core.network import NetworkProbeRequest, diagnose_network


async def test_real_proxy_receives_independent_head_connections(settings):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            received.append((self.path, self.client_address))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    settings["http_proxy"] = f"http://127.0.0.1:{server.server_port}"
    try:
        report = await diagnose_network(NetworkProbeRequest(url="http://network-fixture.invalid/probe", mode="proxy", samples=3))
        assert report.routes[0].status == "passed"
        assert all(item.path == "proxy" for item in report.routes[0].samples)
        assert len(received) == len({address for _, address in received}) == 3
        assert {path for path, _ in received} == {"http://network-fixture.invalid/probe"}
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.fixture
def settings(monkeypatch):
    values = {"proxy_enabled": True, "http_proxy": "http://user:secret@proxy.test:7890"}
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: values.get(key, default)))
    return values


async def test_comparison_reports_actual_fallback_and_http_error(settings, monkeypatch):
    calls = []

    def factory(*, proxy=None, **kwargs):
        def respond(request):
            calls.append((proxy, request.method))
            assert "authorization" not in request.headers
            if proxy is None:
                raise httpx.ConnectTimeout("sensitive request must not appear")
            return httpx.Response(403, headers={"location": "http://private.test/"})
        return httpx.MockTransport(respond)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    report = await diagnose_network(NetworkProbeRequest(samples=2))
    system, direct, proxy = report.routes
    assert system.status == proxy.status == "http_error"
    assert direct.status == "failed"
    assert system.samples[0].path == "proxy" and system.samples[0].attempts == 2
    assert direct.samples[0].path == "direct" and direct.samples[0].error_code == "timeout"
    assert proxy.reachable_count == 2 and proxy.http_ok_count == 0
    assert len(calls) == 8 and {method for _, method in calls} == {"HEAD"}
    assert "secret" not in report.model_dump_json() and "sensitive" not in report.model_dump_json()


async def test_disabled_proxy_does_not_masquerade_as_success(settings, monkeypatch):
    settings["proxy_enabled"] = False
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(lambda r: httpx.Response(204)))
    report = await diagnose_network(NetworkProbeRequest(samples=1))
    assert [route.status for route in report.routes] == ["passed", "passed", "unavailable"]
    assert report.routes[0].samples[0].source == "disabled"
    assert report.routes[2].samples == []


async def test_partial_samples_and_config_change(settings, monkeypatch):
    count = 0

    def factory(**kwargs):
        def respond(request):
            nonlocal count
            count += 1
            settings["proxy_rules"] = "DOMAIN,example.org,DIRECT"
            if count == 2:
                raise httpx.ConnectError("connection lost")
            return httpx.Response(204)
        return httpx.MockTransport(respond)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    report = await diagnose_network(NetworkProbeRequest(mode="proxy", samples=3))
    assert report.configuration_changed
    assert report.routes[0].status == "partial" and report.routes[0].reachable_count == 2


async def test_cancellation_closes_connection(settings, monkeypatch):
    entered = asyncio.Event()
    closed = []

    class Pending(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            entered.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: Pending())
    task = asyncio.create_task(diagnose_network(NetworkProbeRequest(mode="proxy", samples=1)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed == [True]


@pytest.mark.parametrize("data", [
    {"url": "file:///etc/passwd"}, {"url": "https://user:pass@example.com/"},
    {"url": "https://example.com/?token=secret"}, {"url": "https://example.com:bad/"},
    {"samples": 6}, {"samples": 1.5}, {"timeout_seconds": float("nan")},
])
def test_invalid_probe_input(data):
    with pytest.raises(ValidationError):
        NetworkProbeRequest(**data)


async def test_web_and_ai_share_probe_contract(settings, monkeypatch):
    from fastapi import FastAPI

    from entities.entity_query.network import check_network_proxy
    from web.routers.network import router

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(lambda r: httpx.Response(204)))
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/network/diagnose", json={"mode": "proxy", "samples": 1})
        assert response.status_code == 200
        web = response.json()
        invalid = await client.post("/network/diagnose", json={"samples": 9})
        assert invalid.status_code == 422
    ai = json.loads(await check_network_proxy(mode="proxy", samples=1))
    assert web["routes"][0]["status"] == ai["routes"][0]["status"] == "passed"
    assert ai["routes"][0]["samples"][0]["path"] == "proxy"
    assert json.loads(await check_network_proxy(samples=9))["cause"] == "param"
