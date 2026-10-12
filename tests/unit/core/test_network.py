"""统一网络策略的路由、失败回退、配置热更与流生命周期。"""

from __future__ import annotations

import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from core.config import ConfigManager, ConfigRegistry
from core.network import async_network_client, get_proxy_url, network_client, resolve_network_route
from core.network.policy import parse_rules


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    values: dict[str, Any] = {"proxy_enabled": True, "http_proxy": "http://proxy.test:7890"}
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: values.get(key, default)))
    return values


@pytest.mark.parametrize(("url", "expected"), [
    ("https://api.example.com", "proxy"), ("https://example.com", "direct"),
    ("https://www.example.com", "direct"), ("https://fakeexample.com", "fallback"),
    ("http://10.2.3.4", "proxy"), ("http://127.0.0.1", "direct"),
    ("http://printer.local", "direct"), ("http://[::1]", "direct"),
])
def test_ordered_rules_and_local_defaults(settings: dict, url: str, expected: str) -> None:
    settings["proxy_rules"] = "DOMAIN,api.example.com,PROXY\nDOMAIN-SUFFIX,example.com,DIRECT\nIP-CIDR,10.0.0.0/8,PROXY"
    assert resolve_network_route(url).action == expected


def test_modes_and_https_address_share_one_source(settings: dict) -> None:
    assert get_proxy_url("https://example.com") is None
    assert get_proxy_url("https://example.com", "proxy") == settings["http_proxy"]
    settings["https_proxy"] = "socks5://proxy.test:1080"
    assert get_proxy_url("https://example.com", "proxy") == settings["https_proxy"]
    assert get_proxy_url("http://example.com", "proxy") == settings["http_proxy"]
    assert get_proxy_url("https://example.com", "direct") is None
    settings["proxy_enabled"] = False
    assert resolve_network_route("https://example.com").proxies == (None,)
    with pytest.raises(ValueError, match="未启用"):
        get_proxy_url("https://example.com", "proxy")


@pytest.mark.parametrize("rule", ["DOMAIN-SUFFIX,com,unknown", "DOMAIN,https://a.com,PROXY", "IP-CIDR,bad,DIRECT", "DOMAIN,*.example.com,DIRECT", "just anything"])
def test_invalid_rules_rejected_by_shared_write_contract(rule: str) -> None:
    item = ConfigRegistry.get_item("proxy_rules")
    assert item is not None
    with pytest.raises(ValueError, match="第 1 行"):
        item.coerce_value(rule)


def test_domain_normalization_and_environment_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    assert parse_rules("# comment\nDOMAIN-SUFFIX,EXAMPLE.COM.,proxy")[0].target == "example.com"
    item = ConfigRegistry.get_item("http_proxy")
    assert item is not None
    monkeypatch.setenv("TEST_PROXY_ADDRESS", "socks5h://localhost:1080")
    assert item.coerce_value("${TEST_PROXY_ADDRESS}") == "${TEST_PROXY_ADDRESS}"
    with pytest.raises(ValueError):
        item.coerce_value("http://host:7890/a/path")


@pytest.mark.parametrize("async_mode", [False, True])
async def test_connection_failure_falls_back_and_limits_only_connect(
    settings: dict, monkeypatch: pytest.MonkeyPatch, async_mode: bool,
) -> None:
    calls: list[tuple[str | None, dict]] = []
    def factory(*, proxy=None, **kwargs):
        def respond(request: httpx.Request) -> httpx.Response:
            calls.append((proxy, request.extensions["timeout"]))
            if proxy is None:
                raise httpx.ConnectTimeout("offline", request=request)
            return httpx.Response(200, json={"ok": True})
        return httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport" if async_mode else "HTTPTransport", factory)
    if async_mode:
        async with async_network_client(timeout=40) as client:
            response = await client.post("https://example.com", json={"message": "once"})
    else:
        with network_client(timeout=40) as client:
            response = client.post("https://example.com", json={"message": "once"})
    assert [call[0] for call in calls] == [None, settings["http_proxy"]]
    assert calls[0][1]["connect"] == 3 and calls[0][1]["read"] == 40
    assert calls[1][1]["connect"] == 40
    assert response.extensions["network_route"] == {"path": "proxy", "source": "default", "attempts": 2}


@pytest.mark.parametrize(("method", "failure", "attempts"), [
    ("POST", "read_timeout", 1), ("GET", "read_timeout", 2),
    ("GET", "certificate", 1), ("POST", "status", 1), ("GET", "status", 1),
])
async def test_replay_boundaries(settings: dict, monkeypatch: pytest.MonkeyPatch, method: str, failure: str, attempts: int) -> None:
    calls = []
    def factory(*, proxy=None, **kwargs):
        def respond(request):
            calls.append(proxy)
            if failure == "status":
                return httpx.Response(403)
            if proxy:
                return httpx.Response(200)
            if failure == "certificate":
                raise httpx.ConnectError("invalid certificate") from ssl.SSLCertVerificationError("bad cert")
            raise httpx.ReadTimeout("response missing")
        return httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", factory)
    async with async_network_client() as client:
        if failure != "status" and attempts == 1:
            with pytest.raises(httpx.TransportError):
                await client.request(method, "https://example.com")
        else:
            await client.request(method, "https://example.com")
    assert len(calls) == attempts


async def test_hot_change_preserves_inflight_stream_and_retires_idle_pool(settings: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[str | None] = []
    opened: list[str | None] = []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"still streaming"
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self, proxy=None, **kwargs):
            self.proxy = proxy
            opened.append(proxy)
        async def handle_async_request(self, request):
            return httpx.Response(200, stream=Stream())
        async def aclose(self):
            closed.append(self.proxy)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", Transport)
    async with async_network_client(proxy_mode="proxy") as client:
        old = await client.send(client.build_request("GET", "https://example.com"), stream=True)
        settings["http_proxy"] = "http://new-proxy.test:7890"
        await client.get("https://example.com")
        assert closed == []
        assert await old.aread() == b"still streaming"
        await old.aclose()
        await client.get("https://example.com")
        assert closed == ["http://proxy.test:7890"]
    assert opened == ["http://proxy.test:7890", "http://new-proxy.test:7890"]
    assert closed == opened


async def test_real_http_proxy_and_httpx2_share_rules(settings: dict) -> None:
    import httpx2

    from core.network.httpx2 import AsyncNetworkTransport2

    paths: list[str] = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.update(http_proxy=f"http://127.0.0.1:{server.server_port}", proxy_rules="DOMAIN,network-fixture.invalid,PROXY")
    try:
        with network_client() as client:
            assert client.get("http://network-fixture.invalid/sync").text == "ok"
        async with async_network_client() as client:
            assert (await client.get("http://network-fixture.invalid/async")).text == "ok"
        async with httpx2.AsyncClient(transport=AsyncNetworkTransport2(), trust_env=False) as client2:
            assert (await client2.get("http://network-fixture.invalid/mcp")).text == "ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert paths == [f"http://network-fixture.invalid/{name}" for name in ("sync", "async", "mcp")]
