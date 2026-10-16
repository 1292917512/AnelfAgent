"""组件凭据中心测试：注册/读写/脱敏/未登记条目。"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
        pk.register_provider_key("minimax", "sound", "MiniMax 平台", "说明", fields=(pk.CredentialField("api_key"),))
        pk.set_provider_key("minimax", "api_key", "sk-abcdefghijklmnop")
        entries = pk.list_provider_keys("sound")
        assert len(entries) == 1
        entry = entries[0]
        assert entry["name"] == "minimax" and entry["domain"] == "sound"
        field = entry["fields"][0]
        assert field["configured"] is True
        assert "…" in field["value"] and "abcdefghijklmnop" not in field["value"]

    def test_domain_filter(self) -> None:
        pk.register_provider_key("a", "sound", "A", "", fields=(pk.CredentialField("api_key"),))
        pk.register_provider_key("b", "vision", "B", "", fields=(pk.CredentialField("api_key"),))
        assert {e["name"] for e in pk.list_provider_keys("sound")} == {"a"}
        assert len(pk.list_provider_keys()) == 2

    def test_empty_value_clears_field(self) -> None:
        pk.register_provider_key("a", "sound", "A", "", fields=(pk.CredentialField("api_key"),))
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
            fields=(pk.CredentialField("api_key"), pk.CredentialField("api_host", "接入点", secret=False)))
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


def test_module_ownership_reload_and_rollback(monkeypatch):
    from core.registration import registration_transaction

    monkeypatch.setattr(pk, "caller_module", lambda: "entities.sample.credentials")
    fields = (pk.CredentialField("endpoint", secret=False),)
    pk.register_provider_key("sample", "network", "Sample", fields=fields, destination="/entities/sample")
    pk.set_provider_key("sample", "endpoint", "https://example.test")
    with pytest.raises(RuntimeError), registration_transaction("entities.sample"):
        pk.register_provider_key("sample", "network", "Changed", fields=fields)
        raise RuntimeError("abort")
    assert pk.list_provider_keys()[0]["title"] == "Sample"
    assert pk.list_provider_keys()[0]["owner"] == "entity:sample"
    pk.unregister_provider_keys_by_module("entities.sam")
    assert not pk.list_provider_keys()[0]["unregistered"]
    pk.unregister_provider_keys_by_module("entities.sample")
    assert pk.list_provider_keys()[0]["fields"][0]["secret"]
    pk.register_provider_key("sample", "network", "Sample", fields=fields)
    assert pk.list_provider_keys()[0]["fields"][0]["value"] == "https://example.test"


def test_declared_fields_status_atomic_updates_and_no_secret_echo():
    pk.register_provider_key("service", "network", "Service", fields=(
        pk.CredentialField("endpoint", secret=False), pk.CredentialField("token", required=False),
    ))
    pk.set_provider_fields("service", {"endpoint": "https://service.test", "token": "private-test-token"})
    entry = pk.list_provider_keys()[0]
    assert entry["configured"] and len(entry["fields"]) == 2
    assert "private-test-token" not in str(entry)
    with pytest.raises(ValueError):
        pk.set_provider_fields("service", {"endpoint": "changed", "unknown": "invalid"})
    assert pk.get_provider_key("service", "endpoint") == "https://service.test"
    pk.set_provider_fields("service", {"token": ""})
    assert pk.get_provider_key("service", "endpoint") and not pk.get_provider_key("service", "token")
    assert pk.list_provider_keys()[0]["configured"]


def test_parallel_writers_and_invalid_file_preservation():
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: pk.set_provider_key("sample", f"field_{index}", str(index)), range(32)))
    assert len(pk._load()["sample"]) == 32
    Path(pk._path()).write_text("invalid json", encoding="utf-8")
    with pytest.raises(ValueError):
        pk.set_provider_key("sample", "token", "new")
    assert Path(pk._path()).read_text(encoding="utf-8") == "invalid json"


def test_custom_editor_is_owned_and_not_writable_as_fields(monkeypatch):
    monkeypatch.setattr(pk, "caller_module", lambda: "channels.sample.adapter")
    pk.register_provider_key("qr", "channel", "QR", fields=(), editor="scan", destination="/channels?channel=sample")
    entry = pk.list_provider_keys()[0]
    assert entry["owner"] == "channel:sample" and entry["editor"] == "scan" and not entry["fields"]
    with pytest.raises(ValueError):
        pk.set_provider_key("qr", "api_key", "not-allowed")
    monkeypatch.setattr(pk, "caller_module", lambda: "entities.other")
    with pytest.raises(ValueError):
        pk.register_provider_key("qr", "other", "Other", fields=(pk.CredentialField("api_key"),))
