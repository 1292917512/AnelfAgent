"""entities/web web_search 单元测试：MiniMax Coding Plan 为唯一搜索源，错误可诊断。"""

from __future__ import annotations

import asyncio
import json

import pytest

import entities.web.search as search_mod
import entities.web.tools as web_tools
import entities.web.web_config as web_config_mod


class TestWebSearch:
    def test_search_ok(self, monkeypatch: pytest.MonkeyPatch):
        called = []
        monkeypatch.setattr(
            search_mod, "minimax_search",
            lambda q, n: called.append((q, n)) or {
                "query": q, "sources": 1,
                "references": [{"title": "t", "url": "https://a.com", "snippet": "s"}],
            },
        )
        out = json.loads(web_tools.web_search("  query  "))
        assert out["provider"] == "minimax"
        assert out["references"][0]["url"] == "https://a.com"
        assert called == [("query", 8)]

    def test_max_results_clamped(self, monkeypatch: pytest.MonkeyPatch):
        called = []
        monkeypatch.setattr(
            search_mod, "minimax_search",
            lambda q, n: called.append(n) or {"query": q, "sources": 0, "references": []},
        )
        web_tools.web_search("q", max_results=100)
        assert called == [20]

    def test_search_error_diagnosable(self, monkeypatch: pytest.MonkeyPatch):
        def _boom(*a, **kw):
            raise RuntimeError("MiniMax Coding Plan 未配置凭据")
        monkeypatch.setattr(search_mod, "minimax_search", _boom)
        out = json.loads(web_tools.web_search("query"))
        assert "error" in out
        assert "hint" in out
        assert "coding_plan_api_key" in out["hint"]


class TestMinimaxSearch:
    def test_unconfigured_raises(self, monkeypatch: pytest.MonkeyPatch):
        import entities.minimax.client as client_mod
        monkeypatch.setattr(client_mod, "_config_cache", {})
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="coding_plan_api_key"):
            search_mod.minimax_search("q", 5)

    def test_in_running_loop_raises(self, monkeypatch: pytest.MonkeyPatch):
        async def _in_loop() -> None:
            with pytest.raises(RuntimeError, match="事件循环"):
                search_mod.minimax_search("q", 5)
        asyncio.run(_in_loop())


class TestWebConfig:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setattr(web_config_mod, "_CONFIG_FILE", str(tmp_path / "config.json"))
        monkeypatch.setattr(web_config_mod, "_config_cache", None)

    def test_proxy_default_empty(self):
        assert web_config_mod.get_proxy() == ""

    def test_update_roundtrip(self):
        web_config_mod.update_config({"proxy": "http://127.0.0.1:7890"})
        assert web_config_mod.get_proxy() == "http://127.0.0.1:7890"
        assert web_config_mod.get_config()["proxy"] == "http://127.0.0.1:7890"
