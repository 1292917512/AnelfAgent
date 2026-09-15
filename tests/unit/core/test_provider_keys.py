"""组件凭据中心测试：注册/读写/脱敏/未登记条目。"""

from __future__ import annotations

import json
import os

import pytest

from core import provider_keys as pk


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(pk, "_cache", None)
    monkeypatch.setattr(pk, "_cache_mtime", -1.0)
    monkeypatch.setattr(pk, "_registry", {})
    monkeypatch.setattr(pk, "_path", lambda: str(tmp_path / "provider_keys.json"))
    yield


class TestRegistry:
    def test_register_and_mask(self) -> None:
        pk.register_provider_key("minimax", "sound", "MiniMax 平台", "说明")
        pk.set_provider_key("minimax", "api_key", "sk-abcdefghijklmnop")
        entries = pk.list_provider_keys("sound")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["name"] == "minimax" and entry["domain"] == "sound"
        field = entry["fields"][0]
        assert field["configured"] is True
        assert "…" in field["value"] and "abcdefghijklmnop" not in field["value"]

    def test_domain_filter(self) -> None:
        pk.register_provider_key("a", "sound", "A", "")
        pk.register_provider_key("b", "vision", "B", "")
        assert {e["name"] for e in pk.list_provider_keys("sound")} == {"a"}
        assert len(pk.list_provider_keys()) == 2

    def test_empty_value_clears_field(self) -> None:
        pk.register_provider_key("a", "sound", "A", "")
        pk.set_provider_key("a", "api_key", "sk-x")
        pk.set_provider_key("a", "api_key", "")
        assert pk.get_provider_key("a") == ""
        entry = pk.list_provider_keys()[0]  # 登记条目仍在（未配置态）
        assert entry["fields"][0]["configured"] is False
        # 存储侧：字段清空后条目整体摘除（文件不再残留空壳）
        assert "a" not in pk._load()

    def test_extra_fields(self) -> None:
        pk.register_provider_key(
            "cp", "sound", "CP", "",
            extra_fields=[{"key": "api_host", "label": "接入点", "secret": False}])
        pk.set_provider_key("cp", "api_key", "sk-y")
        pk.set_provider_key("cp", "api_host", "https://sub.example.com")
        entry = pk.list_provider_keys()[0]
        hosts = [f for f in entry["fields"] if f["key"] == "api_host"]
        assert hosts[0]["value"] == "https://sub.example.com"  # 非敏感不脱敏
        assert pk.get_provider_key("cp", field="api_host") == "https://sub.example.com"

    def test_unregistered_file_entries_listed(self) -> None:
        path = pk._path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"ghost": {"api_key": "sk-ghostsecret"}}, fh)
        pk._cache = None
        entries = pk.list_provider_keys()
        ghost = entries[0]
        assert ghost["unregistered"] is True
        assert "ghostsecret" not in ghost["fields"][0]["value"]

    def test_missing_file_returns_empty(self) -> None:
        assert pk.get_provider_key("nobody") == ""
        assert pk.list_provider_keys() == []
