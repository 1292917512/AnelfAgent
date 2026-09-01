"""entities/web 工具层与矩阵路由单元测试（提供者以替身注入注册表）。"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple

import pytest

import entities.web.providers as providers_mod
import entities.web.tools as web_tools
import entities.web.web_config as web_config_mod
from entities.web.providers.base import CAP_READER, CAP_REPO, CAP_SEARCH, Provider


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
def isolate_web_config(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(web_config_mod, "_CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(web_config_mod, "_config_cache", None)


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch):
    def _install(providers: Dict[str, FakeProvider]) -> Dict[str, FakeProvider]:
        monkeypatch.setattr(providers_mod, "_PROVIDERS", providers)
        return providers
    return _install


class TestWebSearch:
    def test_search_ok_uses_active_provider(self, fake_registry):
        fake = FakeProvider("minimax")
        fake_registry({"minimax": fake})
        out = json.loads(web_tools.web_search("  query  "))
        assert out["provider"] == "minimax"
        assert out["references"][0]["url"] == "https://a.com"
        assert fake.search_calls == [("query", 8)]

    def test_provider_param_override(self, fake_registry):
        first = FakeProvider("minimax")
        second = FakeProvider("bigmodel", source="llm")
        fake_registry({"minimax": first, "bigmodel": second})
        out = json.loads(web_tools.web_search("q", provider="bigmodel"))
        assert out["provider"] == "bigmodel"
        assert second.search_calls == [("q", 8)]
        assert first.search_calls == []

    def test_max_results_clamped(self, fake_registry):
        fake = FakeProvider("minimax")
        fake_registry({"minimax": fake})
        web_tools.web_search("q", max_results=100)
        assert fake.search_calls == [("q", 20)]

    def test_unknown_provider_error(self, fake_registry):
        fake_registry({"minimax": FakeProvider("minimax")})
        out = json.loads(web_tools.web_search("q", provider="nope"))
        assert "未知提供者" in out["error"]

    def test_search_error_classified_by_provider(self, fake_registry):
        fake = FakeProvider("minimax", boom=RuntimeError("未配置凭据"))
        fake_registry({"minimax": fake})
        out = json.loads(web_tools.web_search("q"))
        assert "凭据" in out["error"]


class TestWebFetch:
    def test_fetch_delegates_and_slices(self, fake_registry):
        fake = FakeProvider("builtin")
        fake_registry({"builtin": fake})
        out = json.loads(web_tools.web_fetch("https://a.com", max_chars=10, start_index=5))
        assert out["provider"] == "builtin"
        assert out["content"] == "x" * 10
        assert out["truncated"] is True
        assert out["next_start_index"] == 15
        assert fake.read_calls[0]["url"] == "https://a.com"

    def test_fetch_full_content_not_truncated(self, fake_registry):
        fake = FakeProvider("builtin", read_payload={"url": "https://a.com", "content": "short"})
        fake_registry({"builtin": fake})
        out = json.loads(web_tools.web_fetch("https://a.com"))
        assert out["content"] == "short"
        assert out["truncated"] is False
        assert "next_start_index" not in out

    def test_fetch_rejects_non_http(self, fake_registry):
        fake_registry({"builtin": FakeProvider("builtin")})
        out = json.loads(web_tools.web_fetch("ftp://a.com"))
        assert "仅支持 http/https" in out["error"]

    def test_fetch_error_classified_by_provider(self, fake_registry):
        fake = FakeProvider("builtin", boom=RuntimeError("boom"))
        fake_registry({"builtin": fake})
        out = json.loads(web_tools.web_fetch("https://a.com"))
        assert "error" in out


class TestRepoDocs:
    def test_actions_dispatch(self, fake_registry):
        fake_registry({"bigmodel": FakeProvider("bigmodel")})
        out = json.loads(web_tools.repo_docs("search_doc", "https://github.com/vitejs/vite/", query="如何插件化"))
        assert out["content"] == "doc:vitejs/vite:如何插件化"
        assert out["provider"] == "bigmodel"
        out = json.loads(web_tools.repo_docs("get_structure", "vitejs/vite", dir_path="src"))
        assert out["content"] == "tree:vitejs/vite:src"
        out = json.loads(web_tools.repo_docs("read_file", "vitejs/vite", path="src/index.ts"))
        assert out["content"] == "file:vitejs/vite:src/index.ts"

    def test_validation(self, fake_registry):
        fake_registry({"bigmodel": FakeProvider("bigmodel")})
        assert "owner/repo" in json.loads(web_tools.repo_docs("search_doc", "nope"))["error"]
        assert "query" in json.loads(web_tools.repo_docs("search_doc", "a/b"))["error"]
        assert "path" in json.loads(web_tools.repo_docs("read_file", "a/b"))["error"]
        assert "未知操作" in json.loads(web_tools.repo_docs("boom", "a/b"))["error"]

    def test_gate_closed_without_repo_provider(self, fake_registry):
        class SearchOnly(Provider):
            name = "x"
            requires_credential = False

            def credential(self) -> Tuple[str, str]:
                return "", ""

            def set_api_key(self, api_key: str) -> None:
                pass

            def search(self, query: str, max_results: int) -> Dict[str, Any]:
                return {}

        fake_registry({"x": SearchOnly()})  # type: ignore[dict-item]
        assert web_tools._repo_available() is False


class TestWebProvidersTool:
    def test_list_matrix(self, fake_registry):
        fake_registry({
            "builtin": FakeProvider("builtin", key="", requires_credential=False),
            "bigmodel": FakeProvider("bigmodel", source="llm"),
        })
        out = json.loads(web_tools.web_providers())
        assert out["capabilities"] == [CAP_SEARCH, CAP_READER, CAP_REPO]
        assert out["selection"][CAP_SEARCH] == "auto"
        by_name = {p["name"]: p for p in out["providers"]}
        assert by_name["bigmodel"]["capabilities"] == [CAP_SEARCH, CAP_READER, CAP_REPO]
        assert by_name["bigmodel"]["credential_source"] == "llm"
        assert by_name["builtin"]["configured"] is True  # 无需凭据即视为已配置

    def test_switch_and_enable_disable(self, fake_registry):
        fake_registry({
            "minimax": FakeProvider("minimax"),
            "bigmodel": FakeProvider("bigmodel"),
        })
        out = json.loads(web_tools.web_providers(action="switch", capability=CAP_SEARCH, provider="bigmodel"))
        assert out["status"] == "ok"
        assert out["active"][CAP_SEARCH] == "bigmodel"
        assert web_config_mod.get_active(CAP_SEARCH) == "bigmodel"

        # 禁用被固定选择的提供者：显式选择不做隐式回退，active 落空
        out = json.loads(web_tools.web_providers(action="disable", provider="bigmodel"))
        assert out["active"][CAP_SEARCH] is None
        out = json.loads(web_tools.web_providers(action="switch", capability=CAP_SEARCH, provider="bigmodel"))
        assert "已禁用" in out["error"]
        # 恢复自动选择后回落到可用提供者
        out = json.loads(web_tools.web_providers(action="switch", capability=CAP_SEARCH, provider="auto"))
        assert out["active"][CAP_SEARCH] == "minimax"
        out = json.loads(web_tools.web_providers(action="enable", provider="bigmodel"))
        assert out["status"] == "ok"

    def test_set_key_and_clear(self, fake_registry):
        fake = FakeProvider("bigmodel", key="")
        fake_registry({"bigmodel": fake})
        out = json.loads(web_tools.web_providers(action="set_key", provider="bigmodel", api_key="sk-x"))
        assert out["credential"] == "saved"
        assert fake.saved_keys == ["sk-x"]
        out = json.loads(web_tools.web_providers(action="set_key", provider="bigmodel", api_key="clear"))
        assert out["credential"] == "cleared"

    def test_unknown_action(self, fake_registry):
        fake_registry({})
        assert "未知操作" in json.loads(web_tools.web_providers(action="boom"))["error"]


class TestWebRouter:
    """矩阵路由 /entity/web：快照脱敏、切换、启停、凭据、按能力测试。"""

    @pytest.fixture
    def client(self, fake_registry):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from entities.web.router import build_router
        self.fakes = fake_registry({
            "builtin": FakeProvider("builtin", key="", requires_credential=False),
            "bigmodel": FakeProvider("bigmodel"),
        })
        app = FastAPI()
        app.include_router(build_router(), prefix="/entity/web")
        return TestClient(app)

    def test_matrix_masks_credentials(self, client):
        resp = client.get("/entity/web/matrix")
        assert resp.status_code == 200
        assert '"k"' not in resp.text  # 凭据本体绝不出站
        body = resp.json()
        assert body["active"][CAP_SEARCH] == "builtin"  # auto 取首个可用（注册表顺序）
        by_name = {p["name"]: p for p in body["providers"]}
        assert by_name["bigmodel"]["capabilities"] == [CAP_SEARCH, CAP_READER, CAP_REPO]
        assert by_name["builtin"]["requires_credential"] is False

    def test_set_active_and_enabled(self, client):
        assert client.put("/entity/web/active", json={"capability": "nope", "provider": "x"}).status_code == 404
        resp = client.put("/entity/web/active", json={"capability": CAP_SEARCH, "provider": "bigmodel"})
        assert resp.status_code == 200
        assert resp.json()["selection"][CAP_SEARCH] == "bigmodel"
        resp = client.put("/entity/web/providers/bigmodel/enabled", json={"enabled": False})
        assert resp.status_code == 200
        assert resp.json()["active"][CAP_SEARCH] == ""  # 唯一检索提供者被禁用

    def test_credential_endpoint(self, client):
        resp = client.put("/entity/web/providers/bigmodel/credential", json={"api_key": "sk-new"})
        assert resp.status_code == 200
        assert self.fakes["bigmodel"].saved_keys == ["sk-new"]
        # 无需凭据的提供者拒绝写入
        assert client.put("/entity/web/providers/builtin/credential", json={"api_key": "x"}).status_code == 400

    def test_test_endpoint_per_capability(self, client):
        resp = client.post("/entity/web/providers/bigmodel/test", json={"capability": CAP_SEARCH, "input": "q"})
        body = resp.json()
        assert body["ok"] is True
        assert "1 条结果" in body["summary"]
        resp = client.post("/entity/web/providers/bigmodel/test", json={"capability": CAP_REPO, "input": "a/b"})
        assert resp.json()["excerpt"].startswith("tree:a/b")
        resp = client.post("/entity/web/providers/bigmodel/test", json={"capability": CAP_READER})
        assert resp.json()["ok"] is True
