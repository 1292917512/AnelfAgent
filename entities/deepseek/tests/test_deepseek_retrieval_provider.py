"""DeepSeek 检索组件（entities/deepseek）单元测试。

客户端请求/响应整形与错误分类、提供者凭据链与能力分发、实体导入注册；
HTTP 层以 httpx.MockTransport 替身注入。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

import httpx
import pytest

import entities.deepseek.client as client_mod
from entities.deepseek.client import (
    DeepSeekError,
    deepseek_error_response,
    deepseek_fetch,
    deepseek_search,
    normalize_search_results,
    unescape_markdown_punctuation,
)
from entities.deepseek.providers import DeepSeekSearchProvider


def _mock_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Any,
) -> Dict[str, Any]:
    """注入 MockTransport 网络替身，记录最近一次请求供断言。"""
    seen: Dict[str, Any] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization", "")
        seen["json"] = json.loads(request.content.decode("utf-8"))
        return handler(request)

    monkeypatch.setattr(
        client_mod, "async_network_client",
        lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(transport_handler), trust_env=False,
        ),
    )
    return seen


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class TestClientRequests:
    def test_search_posts_query_and_count(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": []})

        seen = _mock_transport(monkeypatch, handler)
        _run(deepseek_search("GLM-5 发布", count=5, api_key="test-fixture-mock-key-001"))
        assert seen["url"] == "https://api.deepseek.com/v1/tools/search"
        assert seen["auth"] == "Bearer test-fixture-mock-key-001"
        assert seen["json"] == {"query": "GLM-5 发布", "count": 5}

    def test_search_count_clamped_to_limit(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": []})

        seen = _mock_transport(monkeypatch, handler)
        _run(deepseek_search("k", count=99, api_key="test-fixture-mock-key-001"))
        assert seen["json"]["count"] == 50

    def test_search_rejects_bad_query(self):
        with pytest.raises(ValueError):
            _run(deepseek_search("   ", api_key="k"))
        with pytest.raises(ValueError):
            _run(deepseek_search("长" * 129, api_key="k"))

    def test_fetch_posts_url(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"title": "T", "content": "# 正文"})

        seen = _mock_transport(monkeypatch, handler)
        data = _run(deepseek_fetch(
            "https://example.com/a", api_key="test-fixture-mock-key-001",
        ))
        assert seen["url"] == "https://api.deepseek.com/v1/tools/fetch"
        assert seen["json"] == {"url": "https://example.com/a"}
        assert data["content"] == "# 正文"

    def test_fetch_rejects_bad_url(self):
        with pytest.raises(ValueError):
            _run(deepseek_fetch("ftp://example.com", api_key="k"))
        with pytest.raises(ValueError):
            _run(deepseek_fetch("https://" + "a" * 9000, api_key="k"))

    def test_http_error_raises_deepseek_error(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "bad key"}})

        _mock_transport(monkeypatch, handler)
        with pytest.raises(DeepSeekError) as exc_info:
            _run(deepseek_search("k", api_key="bad"))
        assert exc_info.value.status_code == 401
        assert "bad key" in str(exc_info.value)


class TestNormalize:
    def test_summary_preferred_and_date_passthrough(self):
        data = {
            "results": [
                {"title": "A", "url": "https://a", "snippet": "短摘要",
                 "summary": "面向模型的长摘要", "date": "2026-10-01T00:00:00Z"},
                {"title": "B", "url": "https://b", "snippet": "仅短摘要"},
            ]
        }
        output = normalize_search_results(data, "q", 8)
        assert output["sources"] == 2
        first, second = output["references"]
        assert first["snippet"] == "面向模型的长摘要"
        assert first["date"] == "2026-10-01T00:00:00Z"
        assert second["snippet"] == "仅短摘要"
        assert "date" not in second

    def test_empty_results_hint(self):
        output = normalize_search_results({"results": []}, "q", 8)
        assert output["sources"] == 0
        assert "hint" in output


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

    def test_plain_content_unchanged(self):
        assert unescape_markdown_punctuation("普通正文，无转义。") == "普通正文，无转义。"

    def test_fetch_applies_normalization(self, monkeypatch: pytest.MonkeyPatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "title": "T", "content": "\\# 标题\\, 正文 \\/ 斜杠",
            })

        _mock_transport(monkeypatch, handler)
        data = _run(deepseek_fetch(
            "https://example.com/a", api_key="test-fixture-mock-key-007",
        ))
        assert data["content"] == "# 标题, 正文 / 斜杠"


class TestProvider:
    def test_credential_chain_order(self, monkeypatch: pytest.MonkeyPatch):
        import entities._sdk as sdk_mod
        import entities.deepseek.providers as providers_mod

        provider = DeepSeekSearchProvider()
        monkeypatch.setattr(sdk_mod, "get_provider_key", lambda name, field="api_key": "")
        monkeypatch.setattr(
            providers_mod, "llm_provider_key",
            lambda *kws: ("test-fixture-mock-key-002", "11"),
        )
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        assert provider.credential() == ("test-fixture-mock-key-002", "llm")

        monkeypatch.setattr(
            providers_mod, "llm_provider_key", lambda *kws: ("", ""),
        )
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-fixture-mock-key-003")
        assert provider.credential() == ("test-fixture-mock-key-003", "env")

        monkeypatch.setattr(
            sdk_mod, "get_provider_key", lambda name, field="api_key": "test-fixture-mock-key-004",
        )
        assert provider.credential() == ("test-fixture-mock-key-004", "config")

    def test_search_normalizes_response(self, monkeypatch: pytest.MonkeyPatch):
        captured: Dict[str, Any] = {}

        async def fake_search(query: str, **kwargs: Any) -> Dict[str, Any]:
            captured.update(kwargs, query=query)
            return {"results": [{"title": "A", "url": "https://a", "snippet": "s"}]}

        monkeypatch.setattr(client_mod, "deepseek_search", fake_search)
        provider = DeepSeekSearchProvider()
        monkeypatch.setattr(
            provider, "credential", lambda: ("test-fixture-mock-key-005", "config"),
        )
        output = provider.search("关键词", 6)
        assert captured["query"] == "关键词"
        assert captured["count"] == 6
        assert captured["api_key"] == "test-fixture-mock-key-005"
        assert output["references"][0]["url"] == "https://a"

    def test_read_markdown_only(self, monkeypatch: pytest.MonkeyPatch):
        async def fake_fetch(url: str, **kwargs: Any) -> Dict[str, Any]:
            return {"title": "页面", "content": "# Markdown"}

        monkeypatch.setattr(client_mod, "deepseek_fetch", fake_fetch)
        provider = DeepSeekSearchProvider()
        monkeypatch.setattr(
            provider, "credential", lambda: ("test-fixture-mock-key-006", "config"),
        )
        result = provider.read("https://example.com")
        assert result == {
            "url": "https://example.com", "extract_mode": "markdown",
            "title": "页面", "content": "# Markdown",
        }
        with pytest.raises(ValueError):
            provider.read("https://example.com", extract_mode="text")
        with pytest.raises(ValueError):
            provider.read("https://example.com", extract_mode="raw")

    def test_unconfigured_raises(self, monkeypatch: pytest.MonkeyPatch):
        import entities.deepseek.providers as providers_mod
        monkeypatch.setattr(
            providers_mod, "llm_provider_key", lambda *kws: ("", ""),
        )
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        import entities._sdk as sdk_mod
        monkeypatch.setattr(sdk_mod, "get_provider_key", lambda name, field="api_key": "")
        provider = DeepSeekSearchProvider()
        with pytest.raises(RuntimeError, match="未配置凭据"):
            provider.search("q", 5)


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
        payload = json.loads(deepseek_error_response(
            DeepSeekError(code, "boom"), "网页搜索",
        ))
        assert payload["cause"] == cause
        assert payload["retryable"] is retryable

    def test_value_error_param(self):
        payload = json.loads(deepseek_error_response(ValueError("bad"), "读取"))
        assert payload["cause"] == "param"
        assert payload["retryable"] is False


class TestComponentRegistration:
    def test_entity_import_registers_retrieval_provider(self):
        """实体包导入即完成检索组件注册（search + reader 双能力）。"""
        import entities.deepseek

        entities.deepseek.register_components()

        from agent.retrieval import providers as retrieval_providers
        from agent.retrieval.providers.base import CAPABILITY_PROTOCOLS

        provider = retrieval_providers.get_provider("deepseek")
        assert isinstance(provider, DeepSeekSearchProvider)
        implemented = [
            cap for cap, proto in CAPABILITY_PROTOCOLS.items()
            if isinstance(provider, proto)
        ]
        assert implemented == ["search", "reader"]
