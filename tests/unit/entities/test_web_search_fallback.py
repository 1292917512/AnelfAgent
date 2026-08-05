"""entities/web web_search 兜底链路单元测试：主源失败自动降级 MiniMax，错误可诊断。"""

from __future__ import annotations

import json

import pytest

import entities.web.baidu_search as baidu_mod
import entities.web.search_fallback as fallback_mod
import entities.web.tools as web_tools


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        baidu_mod, "search_prefer_deep",
        lambda *a, **kw: {"references": [{"title": "t", "url": "https://a.com", "snippet": "s"}], "summary": "总结"},
    )
    monkeypatch.setattr(fallback_mod, "try_minimax_search", lambda *a, **kw: None)


class TestWebSearchFallback:
    def test_baidu_ok_no_fallback(self, monkeypatch: pytest.MonkeyPatch):
        called = []
        monkeypatch.setattr(
            fallback_mod, "try_minimax_search",
            lambda *a, **kw: called.append(1) or None,
        )
        out = json.loads(web_tools.web_search("query"))
        assert out["provider"] == "baidu"
        assert out["summary"] == "总结"
        assert called == []

    def test_fallback_to_minimax(self, monkeypatch: pytest.MonkeyPatch):
        def _baidu_fail(*a, **kw):
            raise ConnectionError("baidu down")
        monkeypatch.setattr(baidu_mod, "search_prefer_deep", _baidu_fail)
        monkeypatch.setattr(
            fallback_mod, "try_minimax_search",
            lambda q, n: {"query": q, "sources": 1, "references": [{"title": "m", "url": "https://m.com", "snippet": "x"}]},
        )
        out = json.loads(web_tools.web_search("query"))
        assert out["provider"] == "minimax"
        assert out["fallback_from"] == "baidu"
        assert "baidu down" in out["primary_error"]
        assert "note" not in out

    def test_fallback_recency_note(self, monkeypatch: pytest.MonkeyPatch):
        def _baidu_fail(*a, **kw):
            raise ConnectionError("baidu down")
        monkeypatch.setattr(baidu_mod, "search_prefer_deep", _baidu_fail)
        monkeypatch.setattr(
            fallback_mod, "try_minimax_search",
            lambda q, n: {"query": q, "sources": 0, "references": []},
        )
        out = json.loads(web_tools.web_search("query", search_recency="week"))
        assert out["provider"] == "minimax"
        assert "search_recency" in out["note"]

    def test_both_down_returns_diagnosable_error(self, monkeypatch: pytest.MonkeyPatch):
        def _baidu_fail(*a, **kw):
            raise ConnectionError("baidu down")
        monkeypatch.setattr(baidu_mod, "search_prefer_deep", _baidu_fail)
        out = json.loads(web_tools.web_search("query"))
        assert "error" in out
        assert "hint" in out
        assert "coding_plan_api_key" in out["hint"]

    def test_fallback_itself_fails_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        """try_minimax_search 内部异常应静默兜底为 None，不抛出。"""
        import entities.minimax.client as client_mod
        monkeypatch.setattr(client_mod, "_config_cache", {})

        def _boom(*a, **kw):
            raise RuntimeError("unexpected")
        monkeypatch.setattr(client_mod, "MiniMaxClient", _boom)
        assert fallback_mod.try_minimax_search("q", 5) is None


class TestWebSearchProvider:
    def test_provider_minimax_direct(self, monkeypatch: pytest.MonkeyPatch):
        called = []
        monkeypatch.setattr(
            fallback_mod, "minimax_search",
            lambda q, n: called.append((q, n)) or {"query": q, "sources": 1, "references": []},
        )
        out = json.loads(web_tools.web_search("query", provider="minimax"))
        assert out["provider"] == "minimax"
        assert called == [("query", 8)]

    def test_provider_minimax_error_diagnosable(self, monkeypatch: pytest.MonkeyPatch):
        def _boom(*a, **kw):
            raise RuntimeError("MiniMax API 错误 [1004]: invalid api key")
        monkeypatch.setattr(fallback_mod, "minimax_search", _boom)
        out = json.loads(web_tools.web_search("query", provider="minimax"))
        assert "error" in out

    def test_provider_baidu_no_fallback(self, monkeypatch: pytest.MonkeyPatch):
        def _baidu_fail(*a, **kw):
            raise ConnectionError("baidu down")
        monkeypatch.setattr(baidu_mod, "search_prefer_deep", _baidu_fail)
        called = []
        monkeypatch.setattr(
            fallback_mod, "try_minimax_search",
            lambda *a, **kw: called.append(1) or None,
        )
        out = json.loads(web_tools.web_search("query", provider="baidu"))
        assert "error" in out
        assert called == []  # provider=baidu 时不走兜底

    def test_provider_unknown(self):
        out = json.loads(web_tools.web_search("query", provider="nope"))
        assert "error" in out
        assert out.get("cause") == "param"
