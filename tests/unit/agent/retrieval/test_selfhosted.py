"""自托管检索服务的线协议、配置隔离及失败语义。"""

import json
from unittest.mock import Mock

import httpx
import pytest

from agent.retrieval import providers, tools
from agent.retrieval.providers import selfhosted
from core.config import ConfigManager


@pytest.fixture
def service_settings(monkeypatch):
    values = {"retrieval_crawl4ai_base_url": "http://127.0.0.1:11235",
              "retrieval_crawl4ai_api_key": "local-service-secret",
              "retrieval_searxng_base_url": "http://127.0.0.1:18080"}
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda key, default=None: values.get(key, default)))
    return values


@pytest.fixture
def wire(monkeypatch):
    requests = []
    state = {"status": 200, "payload": {}}

    def handle(request):
        requests.append(request)
        return httpx.Response(state["status"], json=state["payload"],
                              extensions={"network_route": {"path": "direct", "source": "local", "attempts": 1}})

    client_factory = Mock(side_effect=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False))
    monkeypatch.setattr(selfhosted, "network_client", client_factory)
    monkeypatch.setattr("agent.retrieval.fetcher.check_ssrf_url", lambda url: None)
    return state, requests, client_factory


def test_search_normalizes_results_and_preserves_partial_engine_failure(service_settings, wire):
    state, requests, factory = wire
    state["payload"] = {"results": [
        {"url": "https://example.org/a", "title": "A", "content": "body"},
        {"url": "https://example.org/a", "title": "duplicate"},
        {"url": "javascript:alert(1)", "title": "invalid"},
    ], "unresponsive_engines": [["engine", "timeout"]]}
    result = json.loads(tools.web_search("hello", provider="searxng"))
    assert result["sources"] == 1 and result["references"][0]["snippet"] == "body"
    assert result["unresponsive_engines"] == [["engine", "timeout"]]
    assert requests[0].url.params["format"] == "json"
    assert requests[0].url.params["q"] == "hello"
    assert "authorization" not in requests[0].headers
    assert factory.call_args.kwargs["proxy_mode"] == "direct"


def test_empty_search_distinguishes_engine_failure(service_settings, wire):
    state, _, _ = wire
    state["payload"] = {"results": [], "unresponsive_engines": [["engine", "captcha"]]}
    result = json.loads(tools.web_search("hello", provider="searxng"))
    assert result["code"] == "SEARCH_ENGINES_UNAVAILABLE"
    state["payload"] = {"results": [], "unresponsive_engines": []}
    result = json.loads(tools.web_search("hello", provider="searxng"))
    assert result["sources"] == 0 and "error" not in result


def test_browser_payload_and_bounded_tool_result(service_settings, wire):
    state, requests, factory = wire
    state["payload"] = {"success": True, "results": [{
        "success": True, "status_code": 200, "url": "https://example.org",
        "markdown": {"raw_markdown": "0123456789"}, "metadata": {"title": "Example"},
        "links": {"internal": [{"href": "https://example.org/a", "text": "A"}]},
    }]}
    result = json.loads(tools.web_crawl("https://example.org", css_selector="article", wait_for=".ready",
                        scan_full_page=True, max_chars=4, start_index=2, proxy_mode="direct"))
    assert result["content"] == "2345" and result["next_start_index"] == 6
    assert result["links"] == [{"url": "https://example.org/a", "title": "A"}]
    payload = json.loads(requests[0].content)
    config = payload["crawler_config"]["params"]
    assert config["wait_for"] == "css:.ready" and config["css_selector"] == "article"
    assert config["cache_mode"] == {"type": "CacheMode", "params": "bypass"}
    assert config["scan_full_page"] is True and config["max_scroll_steps"] == 10
    assert config["page_timeout"] == 30000
    assert "browser_config" not in payload and "js_code" not in config
    assert requests[0].headers["authorization"] == "Bearer local-service-secret"
    assert factory.call_args.kwargs["timeout"] == 35
    assert "local-service-secret" not in json.dumps(result)
    read = json.loads(tools.web_fetch("https://example.org", provider="crawl4ai"))
    assert read["content"] == "0123456789"


@pytest.mark.parametrize("page,code", [
    ({"success": False, "status_code": 412, "error_message": "rejected"}, "PAGE_FETCH_FAILED"),
    ({"success": True, "status_code": 404, "markdown": "error page"}, "PAGE_FETCH_FAILED"),
    ({"success": True, "status_code": 200, "markdown": {"raw_markdown": ""}}, "EMPTY_CONTENT"),
])
def test_browser_failure_is_not_successful_content(service_settings, wire, page, code):
    state, _, _ = wire
    state["payload"] = {"success": True, "results": [page]}
    result = json.loads(tools.web_crawl("https://example.org"))
    assert result["code"] == code and "content" not in result
    assert result["service_route"]["path"] == "direct"


@pytest.mark.parametrize("status", [302, 401, 403, 404, 500])
def test_service_error_is_bounded_and_does_not_replay(service_settings, wire, status):
    state, requests, _ = wire
    state.update(status=status, payload={"detail": "local-service-secret " + "x" * 2000})
    result = json.loads(tools.web_crawl("https://example.org"))
    assert result["code"] == f"SERVICE_HTTP_{status}" and result["status_code"] == status
    assert "local-service-secret" not in json.dumps(result) and len(result["body"]) <= 500
    assert len(requests) == 1


def test_private_page_blocked_but_service_address_allowed(service_settings, wire, monkeypatch):
    _, requests, _ = wire
    monkeypatch.setattr("agent.retrieval.fetcher.check_ssrf_url", lambda url: "private target blocked")
    result = json.loads(tools.web_crawl("http://192.168.1.1/admin"))
    assert result["cause"] == "permission" and not requests


def test_missing_service_configuration_does_not_take_over_existing_reader(service_settings):
    service_settings.clear()
    assert providers.resolve("reader").name == "builtin"
    assert tools._crawl_available() is False
    result = json.loads(tools.web_crawl("https://example.org"))
    assert result["cause"] == "config"
    service_settings["retrieval_searxng_base_url"] = "http://127.0.0.1:18080"
    assert providers.resolve("search").name == "searxng"


@pytest.mark.parametrize("endpoint", ["file:///tmp/config", "http://user:pass@host", "https://example.org?key=secret"])
def test_service_url_rejects_embedded_credentials_and_non_http(endpoint):
    with pytest.raises(ValueError):
        selfhosted.validate_service_url(endpoint)


def test_response_size_is_bounded(service_settings, wire):
    state, _, _ = wire
    state["payload"] = {"data": "x" * (8 * 1024 * 1024)}
    result = json.loads(tools.web_search("hello", provider="searxng"))
    assert result["code"] == "RESPONSE_TOO_LARGE"
