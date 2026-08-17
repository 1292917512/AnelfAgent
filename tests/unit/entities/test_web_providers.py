"""entities/web/providers 单元测试：注册表解析（含启停）、凭据回退链、智谱负载解析。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, Tuple

import pytest

import entities.web.providers as providers_mod
import entities.web.providers.bigmodel as bigmodel_mod
import entities.web.providers.minimax as minimax_mod
import entities.web.web_config as web_config_mod
from entities.web.providers.base import (
    CAP_READER,
    CAP_SEARCH,
    Provider,
    llm_provider_key,
)
from entities.web.providers.builtin import BuiltinProvider


class StubProvider(Provider):
    """可控替身：能力由方法子集决定，凭据/启停可编排。"""

    def __init__(self, name: str, key: str = "", requires_credential: bool = True) -> None:
        self.name = name
        self.display_name = name
        self.key_hint = f"{name} 凭据指引"
        self.requires_credential = requires_credential
        self._key = key
        self.saved_keys: list[str] = []

    def credential(self) -> Tuple[str, str]:
        return (self._key, "config") if self._key else ("", "")

    def set_api_key(self, api_key: str) -> None:
        self.saved_keys.append(api_key)
        self._key = api_key


class SearchStub(StubProvider):
    def search(self, query: str, max_results: int) -> Dict[str, Any]:
        return {"query": query, "sources": 0, "references": []}


class ReaderStub(StubProvider):
    def read(self, url: str, **kwargs: Any) -> Dict[str, Any]:
        return {"url": url, "content": ""}


@pytest.fixture(autouse=True)
def isolate_web_config(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(web_config_mod, "_CONFIG_FILE", str(tmp_path / "config.json"))
    monkeypatch.setattr(web_config_mod, "_config_cache", None)


class TestRegistry:
    def test_capability_detection_by_protocol(self):
        assert providers_mod.provider_capabilities(SearchStub("a")) == [CAP_SEARCH]
        assert providers_mod.provider_capabilities(ReaderStub("b")) == [CAP_READER]
        assert providers_mod.provider_capabilities(BuiltinProvider()) == [CAP_READER]

    def test_auto_picks_first_usable(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(providers_mod, "_PROVIDERS", {
            "a": SearchStub("a"),
            "b": SearchStub("b", key="k"),
        })
        assert providers_mod.resolve(CAP_SEARCH).name == "b"

    def test_disabled_excluded_from_auto(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(providers_mod, "_PROVIDERS", {
            "a": SearchStub("a", key="k"),
            "b": SearchStub("b", key="k"),
        })
        web_config_mod.set_enabled("a", False)
        assert providers_mod.resolve(CAP_SEARCH).name == "b"

    def test_explicit_disabled_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(providers_mod, "_PROVIDERS", {"a": SearchStub("a", key="k")})
        web_config_mod.set_enabled("a", False)
        with pytest.raises(ValueError, match="已禁用"):
            providers_mod.resolve(CAP_SEARCH, "a")

    def test_explicit_unsupported_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(providers_mod, "_PROVIDERS", {"a": SearchStub("a", key="k")})
        with pytest.raises(ValueError, match="不支持网页读取能力"):
            providers_mod.resolve(CAP_READER, "a")

    def test_explicit_unconfigured_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(providers_mod, "_PROVIDERS", {"a": SearchStub("a")})
        with pytest.raises(ValueError, match="未配置凭据"):
            providers_mod.resolve(CAP_SEARCH, "a")

    def test_none_available_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(providers_mod, "_PROVIDERS", {"a": SearchStub("a")})
        with pytest.raises(ValueError, match="没有可用的检索提供者"):
            providers_mod.resolve(CAP_SEARCH)
        assert providers_mod.any_available(CAP_SEARCH) is False

    def test_unknown_capability_and_provider(self):
        with pytest.raises(ValueError, match="未知能力"):
            providers_mod.resolve("nope")
        with pytest.raises(ValueError, match="未知提供者"):
            providers_mod.get_provider("nope")

    def test_configured_selection_honored(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(providers_mod, "_PROVIDERS", {
            "a": SearchStub("a", key="k"),
            "b": SearchStub("b", key="k"),
        })
        web_config_mod.set_active(CAP_SEARCH, "b")
        assert providers_mod.resolve(CAP_SEARCH).name == "b"


class TestWebConfig:
    def test_active_roundtrip(self):
        assert web_config_mod.get_active(CAP_SEARCH) == "auto"
        web_config_mod.set_active(CAP_SEARCH, "bigmodel")
        web_config_mod.reload_config()
        assert web_config_mod.get_active(CAP_SEARCH) == "bigmodel"
        assert web_config_mod.get_active(CAP_READER) == "auto"  # 其他能力不受影响

    def test_enabled_roundtrip(self):
        assert web_config_mod.is_enabled("bigmodel") is True
        web_config_mod.set_enabled("bigmodel", False)
        web_config_mod.reload_config()
        assert web_config_mod.is_enabled("bigmodel") is False
        web_config_mod.set_enabled("bigmodel", True)
        assert web_config_mod.is_enabled("bigmodel") is True

    def test_provider_key_roundtrip(self):
        assert web_config_mod.get_provider_key("bigmodel") == ""
        web_config_mod.set_provider_key("bigmodel", "sk-test")
        web_config_mod.reload_config()
        assert web_config_mod.get_provider_key("bigmodel") == "sk-test"
        web_config_mod.set_provider_key("bigmodel", "")
        assert web_config_mod.get_provider_key("bigmodel") == ""


class TestLlmProviderKey:
    def _write(self, tmp_path, providers: list) -> str:
        path = tmp_path / "llm_clients.json"
        path.write_text(json.dumps({"providers": providers}), encoding="utf-8")
        return str(path)

    def test_match_by_host_keyword(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setattr("core.path.ConfigPaths.LLM_CLIENTS", self._write(tmp_path, [
            {"id": "zhipu", "base_url": "https://open.bigmodel.cn/api/coding/paas/v4", "api_key": "glm-key"},
            {"id": "other", "base_url": "https://api.deepseek.com", "api_key": "ds-key"},
        ]))
        assert llm_provider_key("bigmodel.cn") == ("glm-key", "zhipu")

    def test_no_match_or_missing_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setattr("core.path.ConfigPaths.LLM_CLIENTS", self._write(tmp_path, [
            {"id": "other", "base_url": "https://api.deepseek.com", "api_key": "ds-key"},
        ]))
        assert llm_provider_key("bigmodel.cn") == ("", "")
        monkeypatch.setattr("core.path.ConfigPaths.LLM_CLIENTS", str(tmp_path / "nope.json"))
        assert llm_provider_key("bigmodel.cn") == ("", "")


class TestProviderCredentials:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.delenv("BIGMODEL_API_KEY", raising=False)

    def test_bigmodel_chain_config_first(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(bigmodel_mod, "llm_provider_key", lambda *a: ("llm-key", "zhipu"))
        provider = bigmodel_mod.BigModelProvider()
        assert provider.credential() == ("llm-key", "llm")
        provider.set_api_key("cfg-key")
        assert provider.credential() == ("cfg-key", "config")
        assert web_config_mod.get_provider_key("bigmodel") == "cfg-key"

    def test_bigmodel_env_fallback(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(bigmodel_mod, "llm_provider_key", lambda *a: ("", ""))
        monkeypatch.setenv("BIGMODEL_API_KEY", "env-key")
        assert bigmodel_mod.BigModelProvider().credential() == ("env-key", "env")

    def test_minimax_llm_fallback(self, monkeypatch: pytest.MonkeyPatch):
        import entities.minimax.client as client_mod
        monkeypatch.setattr(client_mod, "_config_cache", {})
        monkeypatch.setattr(minimax_mod, "llm_provider_key", lambda *a: ("mm-key", "minimax"))
        assert minimax_mod.MinimaxProvider().credential() == ("mm-key", "llm")

    def test_minimax_unconfigured_raises(self, monkeypatch: pytest.MonkeyPatch):
        import entities.minimax.client as client_mod
        monkeypatch.setattr(client_mod, "_config_cache", {})
        monkeypatch.setattr(minimax_mod, "llm_provider_key", lambda *a: ("", ""))
        with pytest.raises(RuntimeError, match="coding_plan_api_key"):
            minimax_mod.MinimaxProvider().search("q", 5)

    def test_builtin_no_credential(self):
        provider = BuiltinProvider()
        assert provider.configured() is True
        with pytest.raises(NotImplementedError):
            provider.set_api_key("x")

    def test_search_in_running_loop_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(bigmodel_mod, "llm_provider_key", lambda *a: ("k", "zhipu"))
        async def _in_loop() -> None:
            with pytest.raises(RuntimeError, match="事件循环"):
                bigmodel_mod.BigModelProvider().search("q", 5)
        asyncio.run(_in_loop())


def _text_result(text: str) -> Any:
    return SimpleNamespace(
        isError=False,
        content=[SimpleNamespace(type="text", text=text)],
        structuredContent=None,
    )


class TestBigModelPayload:
    def test_structured_content_preferred(self):
        result = SimpleNamespace(
            isError=False, content=[], structuredContent={"results": [{"title": "T", "link": "https://a.com", "content": "S"}]},
        )
        payload = bigmodel_mod._payload_from_result(result)
        assert isinstance(payload, dict)
        assert bigmodel_mod._find_result_list(payload)[0]["link"] == "https://a.com"

    def test_double_encoded_json(self):
        inner = json.dumps([{"title": "T", "link": "https://a.com", "content": "S"}])
        payload = bigmodel_mod._payload_from_result(_text_result(json.dumps(inner)))
        assert isinstance(payload, list)
        assert bigmodel_mod._map_item(payload[0]) == {
            "title": "T", "url": "https://a.com", "snippet": "S", "date": "",
        }

    def test_plain_text_returned_as_is(self):
        assert bigmodel_mod._payload_from_result(_text_result("纯文本")) == "纯文本"

    def test_payload_text_extraction(self):
        assert bigmodel_mod._payload_text({"content": "正文"}) == "正文"
        assert bigmodel_mod._payload_text("文本") == "文本"
        assert json.loads(bigmodel_mod._payload_text({"other": 1})) == {"other": 1}
