"""检索核心（agent.retrieval）单元测试：注册表解析、工具层、重排序、矩阵服务。

配置经 monkeypatch 的内存态 ConfigManager 隔离；提供者以替身注入注册表。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

import pytest

import agent.retrieval.providers as providers_mod
import agent.retrieval.tools as retrieval_tools
from agent.retrieval.providers.base import CAP_READER, CAP_REPO, CAP_SEARCH, Provider
from agent.retrieval.providers.builtin import BuiltinProvider


class FakeProvider(Provider):
    """全能力可控替身：预置凭据/结果/异常，记录调用与凭据写入。"""

    def __init__(
        self,
        name: str,
        key: str = "k",
        source: str = "config",
        requires_credential: bool = True,
        search_payload: Optional[Dict[str, Any]] = None,
        read_payload: Optional[Dict[str, Any]] = None,
        boom: Optional[Exception] = None,
    ) -> None:
        self.name = name
        self.display_name = name
        self.description = ""
        self.key_hint = f"{name} 凭据指引"
        self.requires_credential = requires_credential
        self._key = key
        self._source = source
        self._search_payload = search_payload
        self._read_payload = read_payload
        self._boom = boom
        self.search_calls: list[Tuple[str, int]] = []
        self.read_calls: list[Dict[str, Any]] = []
        self.saved_keys: list[str] = []

    def credential(self) -> Tuple[str, str]:
        return (self._key, self._source) if self._key else ("", "")

    def set_api_key(self, api_key: str) -> None:
        self.saved_keys.append(api_key)
        self._key = api_key
        self._source = "config" if api_key else ""

    def search(self, query: str, max_results: int) -> Dict[str, Any]:
        self.search_calls.append((query, max_results))
        if self._boom is not None:
            raise self._boom
        return dict(self._search_payload or {
            "query": query,
            "sources": 1,
            "references": [{"title": "t", "url": "https://a.com", "snippet": "s"}],
        })

    def read(self, url: str, **kwargs: Any) -> Dict[str, Any]:
        self.read_calls.append({"url": url, **kwargs})
        if self._boom is not None:
            raise self._boom
        return dict(self._read_payload or {"url": url, "title": "T", "content": "x" * 100})

    def search_doc(self, repo: str, query: str) -> str:
        return f"doc:{repo}:{query}"

    def get_repo_structure(self, repo: str, dir_path: str = "") -> str:
        return f"tree:{repo}:{dir_path}"

    def read_repo_file(self, repo: str, path: str) -> str:
        return f"file:{repo}:{path}"


@pytest.fixture(autouse=True)
def mem_config(monkeypatch: pytest.MonkeyPatch):
    """内存态配置隔离（ConfigManager 全部读写改道字典，不落盘）。"""
    from core.config import ConfigManager
    store: Dict[str, Any] = {}
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: store.get(k, d)))
    monkeypatch.setattr(ConfigManager, "set", staticmethod(lambda k, v: store.__setitem__(k, v)))
    monkeypatch.setattr(ConfigManager, "has", staticmethod(lambda k: k in store))
    monkeypatch.setattr(ConfigManager, "save", staticmethod(lambda: True))
    return store


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch):
    saved = dict(providers_mod._PROVIDERS)
    providers_mod._PROVIDERS.clear()

    def _install(providers: Dict[str, FakeProvider]) -> Dict[str, FakeProvider]:
        providers_mod._PROVIDERS.clear()
        providers_mod._PROVIDERS.update(providers)
        return providers

    yield _install
    providers_mod._PROVIDERS.clear()
    providers_mod._PROVIDERS.update(saved)


class TestRegistry:
    def test_capability_detection_by_protocol(self):
        assert providers_mod.provider_capabilities(FakeProvider("a")) == [
            CAP_SEARCH, CAP_READER, CAP_REPO,
        ]
        assert providers_mod.provider_capabilities(BuiltinProvider()) == [CAP_READER]

    def test_auto_picks_first_usable(self, fake_registry):
        fake_registry({"a": FakeProvider("a", key=""), "b": FakeProvider("b", key="k")})
        assert providers_mod.resolve(CAP_SEARCH).name == "b"

    def test_disabled_excluded_from_auto(self, fake_registry, mem_config):
        fake_registry({"a": FakeProvider("a"), "b": FakeProvider("b")})
        from agent.retrieval.config import set_enabled
        set_enabled("a", False)
        assert providers_mod.resolve(CAP_SEARCH).name == "b"

    def test_explicit_disabled_rejected(self, fake_registry, mem_config):
        fake_registry({"a": FakeProvider("a")})
        from agent.retrieval.config import set_enabled
        set_enabled("a", False)
        with pytest.raises(ValueError, match="已禁用"):
            providers_mod.resolve(CAP_SEARCH, "a")

    def test_explicit_unsupported_rejected(self, fake_registry):
        # SearchCap 之外的协议未实现 → isinstance 判定失败
        class _OnlySearch(Provider):
            name = "only"
            key_hint = ""

            def credential(self) -> Tuple[str, str]:
                return ("k", "config")

            def set_api_key(self, api_key: str) -> None:
                pass

            def search(self, query: str, max_results: int) -> Dict[str, Any]:
                return {}

        fake_registry({"only": _OnlySearch()})
        with pytest.raises(ValueError, match="不支持网页读取能力"):
            providers_mod.resolve(CAP_READER, "only")

    def test_component_register_unregister(self, fake_registry):
        fake_registry({})
        providers_mod.register(FakeProvider("comp"))
        assert providers_mod.resolve(CAP_SEARCH).name == "comp"
        providers_mod.unregister("comp")
        with pytest.raises(ValueError, match="无可用"):
            providers_mod.resolve(CAP_SEARCH)


class TestWebSearch:
    def test_search_ok_uses_active_provider(self, fake_registry):
        p = FakeProvider("p1")
        fake_registry({"p1": p})
        out = json.loads(retrieval_tools.web_search("你好"))
        assert out["provider"] == "p1"
        assert out["sources"] == 1
        assert p.search_calls == [("你好", 8)]

    def test_provider_param_override(self, fake_registry):
        a, b = FakeProvider("a"), FakeProvider("b")
        fake_registry({"a": a, "b": b})
        out = json.loads(retrieval_tools.web_search("q", provider="b"))
        assert out["provider"] == "b"
        assert a.search_calls == []

    def test_max_results_clamped(self, fake_registry):
        p = FakeProvider("p1")
        fake_registry({"p1": p})
        retrieval_tools.web_search("q", max_results=99)
        assert p.search_calls[0][1] == 20

    def test_unknown_provider_error(self, fake_registry):
        fake_registry({"p1": FakeProvider("p1")})
        out = json.loads(retrieval_tools.web_search("q", provider="nope"))
        assert "error" in out

    def test_search_error_classified_by_provider(self, fake_registry):
        fake_registry({"p1": FakeProvider("p1", boom=RuntimeError("boom"))})
        out = json.loads(retrieval_tools.web_search("q"))
        assert "error" in out


class TestWebFetch:
    def test_fetch_delegates_and_slices(self, fake_registry):
        p = FakeProvider("p1", read_payload={"url": "https://a.com", "content": "x" * 100})
        fake_registry({"p1": p})
        out = json.loads(retrieval_tools.web_fetch("https://a.com", max_chars=10))
        assert out["provider"] == "p1"
        assert out["content"] == "x" * 10
        assert out["truncated"] is True
        assert out["next_start_index"] == 10

    def test_fetch_full_content_not_truncated(self, fake_registry):
        p = FakeProvider("p1", read_payload={"url": "https://a.com", "content": "short"})
        fake_registry({"p1": p})
        out = json.loads(retrieval_tools.web_fetch("https://a.com"))
        assert out["truncated"] is False
        assert "next_start_index" not in out

    def test_fetch_rejects_non_http(self, fake_registry):
        fake_registry({"p1": FakeProvider("p1")})
        out = json.loads(retrieval_tools.web_fetch("ftp://a.com"))
        assert "error" in out

    def test_fetch_error_classified_by_provider(self, fake_registry):
        fake_registry({"p1": FakeProvider("p1", boom=RuntimeError("boom"))})
        out = json.loads(retrieval_tools.web_fetch("https://a.com"))
        assert "error" in out


class TestRepoDocs:
    def test_actions_dispatch(self, fake_registry):
        fake_registry({"p1": FakeProvider("p1")})
        out = json.loads(retrieval_tools.repo_docs("search_doc", "a/b", query="q"))
        assert out["content"] == "doc:a/b:q"
        out = json.loads(retrieval_tools.repo_docs("get_structure", "a/b", dir_path="src"))
        assert out["content"] == "tree:a/b:src"
        out = json.loads(retrieval_tools.repo_docs("read_file", "a/b", path="x.py"))
        assert out["content"] == "file:a/b:x.py"

    def test_validation(self, fake_registry):
        fake_registry({"p1": FakeProvider("p1")})
        assert "error" in json.loads(retrieval_tools.repo_docs("search_doc", "a/b"))
        assert "error" in json.loads(retrieval_tools.repo_docs("bad_action", "a/b"))
        assert "error" in json.loads(retrieval_tools.repo_docs("search_doc", "norepo"))

    def test_gate_closed_without_repo_provider(self, fake_registry):
        class SearchOnlyProvider(Provider):
            name = "s"
            key_hint = ""

            def credential(self) -> Tuple[str, str]:
                return ("k", "config")

            def set_api_key(self, api_key: str) -> None:
                pass

            def search(self, query: str, max_results: int) -> Dict[str, Any]:
                return {}

        fake_registry({"s": SearchOnlyProvider()})
        assert retrieval_tools._repo_available() is False
        fake_registry({"s": SearchOnlyProvider(), "p1": FakeProvider("p1")})
        assert retrieval_tools._repo_available() is True


class TestRetrievalProvidersTool:
    def test_list_matrix(self, fake_registry):
        fake_registry({"p1": FakeProvider("p1")})
        out = json.loads(retrieval_tools.retrieval_providers("list"))
        assert out["providers"][0]["name"] == "p1"
        assert out["providers"][0]["credential_source"] == "config"

    def test_switch_and_enable_disable(self, fake_registry, mem_config):
        a, b = FakeProvider("a"), FakeProvider("b")
        fake_registry({"a": a, "b": b})
        out = json.loads(retrieval_tools.retrieval_providers("switch", provider="b", capability="search"))
        assert out["status"] == "ok"
        from agent.retrieval.config import get_active
        assert get_active("search") == "b"
        out = json.loads(retrieval_tools.retrieval_providers("disable", provider="b"))
        assert out["status"] == "ok"
        assert json.loads(retrieval_tools.retrieval_providers("switch", provider="b", capability="search")).get("error")

    def test_set_key_and_clear(self, fake_registry):
        p = FakeProvider("p1", key="")
        fake_registry({"p1": p})
        out = json.loads(retrieval_tools.retrieval_providers("set_key", provider="p1", api_key="sk-1"))
        assert out["credential"] == "saved"
        assert p.saved_keys == ["sk-1"]
        out = json.loads(retrieval_tools.retrieval_providers("set_key", provider="p1", api_key="clear"))
        assert out["credential"] == "cleared"

    def test_unknown_action(self, fake_registry):
        fake_registry({"p1": FakeProvider("p1")})
        out = json.loads(retrieval_tools.retrieval_providers("fly"))
        assert "error" in out


class TestRerankSearch:
    async def test_rerank_ok(self, monkeypatch: pytest.MonkeyPatch):
        async def _fake(query: str, docs: list) -> Dict[str, Any]:
            return {"results": [{"index": 0, "relevance_score": 0.9}], "model": "m1"}

        monkeypatch.setattr("agent.retrieval.rerank.rerank_documents", _fake)
        out = json.loads(await retrieval_tools.rerank_search("q", '["d1", "d2"]'))
        assert out["success"] is True
        assert out["model"] == "m1"
        assert out["query"] == "q"

    async def test_rerank_failure_returns_error(self, monkeypatch: pytest.MonkeyPatch):
        async def _fail(query: str, docs: list) -> Dict[str, Any]:
            raise RuntimeError("未配置 rerank 类型模型")

        monkeypatch.setattr("agent.retrieval.rerank.rerank_documents", _fail)
        out = json.loads(await retrieval_tools.rerank_search("q", '["d1"]'))
        assert "error" in out


class TestRetrievalService:
    def test_matrix_masks_credentials(self, fake_registry):
        fake_registry({"p1": FakeProvider("p1")})
        from services.retrieval import get_retrieval_service
        matrix = get_retrieval_service().matrix()
        assert matrix["providers"][0]["credential_source"] == "config"
        assert "k" not in json.dumps(matrix)

    def test_set_active_and_enabled(self, fake_registry, mem_config):
        fake_registry({"a": FakeProvider("a"), "b": FakeProvider("b")})
        from services.retrieval import get_retrieval_service
        svc = get_retrieval_service()
        status, _ = svc.set_active("search", "b")
        assert status == 200
        status, detail = svc.set_active("search", "nope")
        assert status == 400
        status, _ = svc.set_enabled("b", False)
        assert status == 200
        status, detail = svc.set_enabled("nope", True)
        assert status == 404

    def test_settings_roundtrip(self, mem_config):
        from services.retrieval import get_retrieval_service
        svc = get_retrieval_service()
        out = svc.save_settings(proxy="http://127.0.0.1:7890", ssrf_protection=False)
        assert out["proxy"] == "http://127.0.0.1:7890"
        assert out["ssrf_protection"] is False
        assert svc.settings()["proxy"] == "http://127.0.0.1:7890"
