"""DeepSeek 提供者（agent/retrieval/providers/deepseek.py）单元测试。

请求整形与转义归一、错误分类、凭据链与能力分发、内置注册；
HTTP 层以 httpx2.MockTransport 替身注入。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import httpx2
import pytest

import agent.retrieval.providers.deepseek as deepseek_mod
from agent.retrieval.providers.deepseek import (
    DeepSeekError,
    DeepSeekProvider,
    _post_tools,
    unescape_markdown_punctuation,
)


def _mock_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> Dict[str, Any]:
    """注入 MockTransport 网络替身，记录最近一次请求供断言。"""
    seen: Dict[str, Any] = {}
    real_client = httpx2.AsyncClient

    def transport_handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization", "")
        seen["json"] = json.loads(request.content.decode("utf-8"))
        return handler(request)

    monkeypatch.setattr(httpx2, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx2.MockTransport(transport_handler),
        **{key: value for key, value in kwargs.items() if key != "transport"},
    ))
    return seen


class TestPostTools:
    def test_posts_bearer_and_payload(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"ok": True})

        seen = _mock_transport(monkeypatch, handler)
        data = asyncio.run(_post_tools(
            "/v1/tools/search", {"query": "q", "count": 5},
            "test-fixture-mock-key-001", 30.0,
        ))
        assert seen["url"] == "https://api.deepseek.com/v1/tools/search"
        assert seen["auth"] == "Bearer test-fixture-mock-key-001"
        assert seen["json"] == {"query": "q", "count": 5}
        assert data == {"ok": True}

    def test_http_error_raises_deepseek_error(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(401, json={"error": {"message": "bad key"}})

        _mock_transport(monkeypatch, handler)
        with pytest.raises(DeepSeekError) as exc_info:
            asyncio.run(_post_tools("/v1/tools/search", {}, "bad", 30.0))
        assert exc_info.value.status_code == 401
        assert "bad key" in str(exc_info.value)


class TestUnescape:
    def test_single_level_punctuation_restored(self):
        assert unescape_markdown_punctuation(
            r"\>\>\> print\(\"hi\"\) 3\.5 版本"
        ) == '>>> print("hi") 3.5 版本'
        assert unescape_markdown_punctuation(
            r"https:\/\/api\.deepseek\.com\/v1"
        ) == "https://api.deepseek.com/v1"

    def test_double_escape_roundtrip(self):
        # 原始文本的 \" 被生成器编码为 \\\" （四个字符），单趟还原应得回 \"
        assert unescape_markdown_punctuation('\\\\\\"key\\\\\\"') == '\\"key\\"'

    def test_letter_escapes_untouched(self):
        assert unescape_markdown_punctuation(r"\d \n \w \s") == r"\d \n \w \s"


class TestProviderSearch:
    def test_search_normalizes_summary_and_date(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"results": [
                {"title": "A", "url": "https://a", "snippet": "短", "summary": "长摘要",
                 "date": "2026-10-01T00:00:00Z"},
            ]})

        seen = _mock_transport(monkeypatch, handler)
        provider = DeepSeekProvider()
        monkeypatch.setattr(provider, "credential", lambda: ("test-fixture-mock-key-002", "config"))
        output = provider.search("关键词", 6)
        assert seen["json"] == {"query": "关键词", "count": 6}
        ref = output["references"][0]
        assert ref["snippet"] == "长摘要"
        assert ref["date"] == "2026-10-01T00:00:00Z"

    def test_search_count_clamped_and_query_validated(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={"results": []})

        seen = _mock_transport(monkeypatch, handler)
        provider = DeepSeekProvider()
        monkeypatch.setattr(provider, "credential", lambda: ("test-fixture-mock-key-003", "config"))
        output = provider.search("q", 99)
        assert seen["json"]["count"] == 50
        assert output["sources"] == 0
        assert "hint" in output
        with pytest.raises(ValueError):
            provider.search("长" * 129, 5)

    def test_unconfigured_raises(self, monkeypatch: pytest.MonkeyPatch):
        provider = DeepSeekProvider()
        monkeypatch.setattr(deepseek_mod, "llm_provider_key", lambda *kws: ("", ""))
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="未配置凭据"):
            provider.search("q", 5)


class TestProviderRead:
    def test_read_returns_unescaped_markdown(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx2.Request) -> httpx2.Response:
            return httpx2.Response(200, json={
                "title": "页面", "content": "\\# 标题\\, 正文 \\/ 斜杠",
            })

        _mock_transport(monkeypatch, handler)
        provider = DeepSeekProvider()
        monkeypatch.setattr(provider, "credential", lambda: ("test-fixture-mock-key-004", "config"))
        result = provider.read("https://example.com/a")
        assert result == {
            "url": "https://example.com/a", "extract_mode": "markdown",
            "title": "页面", "content": "# 标题, 正文 / 斜杠",
        }

    def test_read_rejects_other_modes_and_bad_url(self, monkeypatch: pytest.MonkeyPatch):
        provider = DeepSeekProvider()
        monkeypatch.setattr(provider, "credential", lambda: ("test-fixture-mock-key-005", "config"))
        with pytest.raises(ValueError):
            provider.read("https://example.com", extract_mode="text")
        with pytest.raises(ValueError):
            provider.read("https://example.com", extract_mode="raw")
        with pytest.raises(ValueError):
            provider.read("ftp://example.com")


class TestCredentialChain:
    def test_resolution_order(self, monkeypatch: pytest.MonkeyPatch):
        import agent.retrieval.config as retrieval_config

        provider = DeepSeekProvider()
        monkeypatch.setattr(retrieval_config, "get_provider_key", lambda name: "")
        monkeypatch.setattr(
            deepseek_mod, "llm_provider_key", lambda *kws: ("test-fixture-mock-key-006", "11"),
        )
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        assert provider.credential() == ("test-fixture-mock-key-006", "llm")

        monkeypatch.setattr(deepseek_mod, "llm_provider_key", lambda *kws: ("", ""))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fixture-mock-key-007")
        assert provider.credential() == ("test-fixture-mock-key-007", "env")

        monkeypatch.setattr(
            retrieval_config, "get_provider_key", lambda name: "test-fixture-mock-key-008",
        )
        assert provider.credential() == ("test-fixture-mock-key-008", "config")


class TestErrorResponse:
    @pytest.mark.parametrize("code,cause,retryable", [
        (400, "param", False),
        (401, "config", False),
        (402, "config", False),
        (403, "permission", False),
        (404, "not_found", False),
        (408, "timeout", True),
        (413, "param", False),
        (429, "network", True),
        (500, "network", True),
    ])
    def test_status_classification(self, code: int, cause: str, retryable: bool):
        payload = json.loads(DeepSeekProvider().error_response(
            DeepSeekError(code, "boom"), "网页搜索",
        ))
        assert payload["cause"] == cause
        assert payload["retryable"] is retryable

    def test_value_error_param(self):
        payload = json.loads(DeepSeekProvider().error_response(ValueError("bad"), "读取"))
        assert payload["cause"] == "param"
        assert payload["retryable"] is False


class TestBuiltinRegistration:
    def test_registered_with_both_capabilities(self):
        """内置注册：包导入即登记 search + reader 双能力。"""
        from agent.retrieval import providers as retrieval_providers
        from agent.retrieval.providers.base import CAPABILITY_PROTOCOLS

        provider = retrieval_providers.get_provider("deepseek")
        assert isinstance(provider, DeepSeekProvider)
        implemented = [
            cap for cap, proto in CAPABILITY_PROTOCOLS.items()
            if isinstance(provider, proto)
        ]
        assert implemented == ["search", "reader"]
