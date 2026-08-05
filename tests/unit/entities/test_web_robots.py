"""entities/web/robots.py 单元测试：robots.txt 合规检查与 TTL 缓存。"""

from __future__ import annotations

import httpx
import pytest

import entities.web.tools as web_tools
from entities.web import robots


class _FakeResp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """按 URL 返回预置响应的 httpx.Client 替身。"""

    responses: dict = {}
    call_count: int = 0
    error: Exception | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args) -> bool:
        return False

    def get(self, url: str) -> _FakeResp:
        type(self).call_count += 1
        if type(self).error is not None:
            raise type(self).error
        return type(self).responses.get(url, _FakeResp(404))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    robots.clear_cache()
    _FakeClient.responses = {}
    _FakeClient.call_count = 0
    _FakeClient.error = None
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    monkeypatch.setattr(web_tools, "_ssrf_protection_enabled", lambda: False)
    yield
    robots.clear_cache()


class TestIsAllowed:
    def test_allow_all(self):
        _FakeClient.responses = {
            "https://example.com/robots.txt": _FakeResp(200, "User-agent: *\nAllow: /\n"),
        }
        allowed, detail = robots.is_allowed("https://example.com/page")
        assert allowed is True
        assert detail == ""

    def test_disallow_path(self):
        _FakeClient.responses = {
            "https://example.com/robots.txt": _FakeResp(
                200, "User-agent: *\nDisallow: /private\n"
            ),
        }
        allowed, detail = robots.is_allowed("https://example.com/private/data")
        assert allowed is False
        assert "robots.txt" in detail
        allowed_public, _ = robots.is_allowed("https://example.com/public/data")
        assert allowed_public is True

    def test_401_403_forbidden(self):
        for code in (401, 403):
            robots.clear_cache()
            _FakeClient.responses = {
                "https://example.com/robots.txt": _FakeResp(code),
            }
            allowed, detail = robots.is_allowed("https://example.com/any")
            assert allowed is False
            assert "401/403" in detail

    def test_404_fail_open(self):
        _FakeClient.responses = {"https://example.com/robots.txt": _FakeResp(404)}
        allowed, _ = robots.is_allowed("https://example.com/page")
        assert allowed is True

    def test_network_error_fail_open(self):
        _FakeClient.error = httpx.ConnectError("connection refused")
        allowed, _ = robots.is_allowed("https://example.com/page")
        assert allowed is True

    def test_non_http_passthrough(self):
        allowed, detail = robots.is_allowed("ftp://example.com/file")
        assert allowed is True
        assert detail == ""
        assert _FakeClient.call_count == 0

    def test_cache_avoids_refetch(self):
        _FakeClient.responses = {
            "https://example.com/robots.txt": _FakeResp(200, "User-agent: *\nAllow: /\n"),
        }
        robots.is_allowed("https://example.com/a")
        robots.is_allowed("https://example.com/b")
        assert _FakeClient.call_count == 1

    def test_ssrf_blocked_fail_open(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(web_tools, "_ssrf_protection_enabled", lambda: True)
        monkeypatch.setattr(web_tools, "_check_ssrf_url", lambda url: "SSRF 防护拦截")
        allowed, _ = robots.is_allowed("https://example.com/page")
        assert allowed is True
        assert _FakeClient.call_count == 0
