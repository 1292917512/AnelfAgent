"""判断目录的共同写入口、凭据保护、默认引用和旧配置迁移。"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.judgment.catalog import load_catalog, public_catalog, select_model, update_catalog
from agent.judgment.engine import load_config
from agent.judgment.tools import configure_judgment, list_judgment_models
from agent.runtime.config_migrate import migrate_judgment_config
from core.path import ConfigPaths


def provider(**changes):
    return {"name": "Local", "base_url": "http://127.0.0.1:8090/v1", "api_key": "",
            "models": {"local": {"model": "StartLux-Decision-27B"}}, **changes}


def test_provider_and_model_crud_preserve_default_and_other_models():
    update_catalog("save_provider", "pc", provider())
    assert select_model() is None
    update_catalog("set_default", "local")
    update_catalog("save_provider", "cloud", provider(name="Cloud", models={}))
    update_catalog("save_model", "cloud-jev", {"provider_id": "cloud", "model": "jev-latest"})
    assert load_config().model_id == "local"
    assert load_config("cloud-jev").provider_id == "cloud"
    update_catalog("save_model", "local", {"provider_id": "cloud", "name": "Moved", "model": "decision"})
    assert load_config().provider_id == "cloud"
    assert not load_catalog().providers["pc"].models
    update_catalog("delete_model", "local")
    assert select_model() is None
    assert "cloud-jev" in load_catalog().providers["cloud"].models
    update_catalog("set_default", "cloud-jev")
    update_catalog("delete_provider", "cloud")
    assert load_catalog().default_model == ""


def test_secret_refs_preserved_by_partial_updates_and_masked_edit(monkeypatch):
    monkeypatch.setenv("JEV_TEST_KEY", "a-private-test-key-with-enough-characters")
    update_catalog("save_provider", "pc", provider(api_key="${JEV_TEST_KEY}"))
    safe = public_catalog()
    assert "a-private-test-key-with-enough-characters" not in json.dumps(safe)
    update_catalog("save_provider", "pc", {"name": "Renamed"})
    update_catalog("save_provider", "pc", {"api_key": safe["providers"]["pc"]["api_key"]})
    assert load_catalog().providers["pc"].api_key == "${JEV_TEST_KEY}"
    assert load_config("local").api_key == "a-private-test-key-with-enough-characters"
    update_catalog("save_provider", "pc", {"api_key": ""})
    assert load_config("local").api_key == ""


@pytest.mark.parametrize("action,identifier,data", [
    ("save_provider", "bad/id", provider()),
    ("save_provider", "other", provider()),
    ("save_provider", "pc", {"base_url": "https://user:password@host"}),
    ("save_provider", "pc", {"models": {"bad": {"model": ""}}}),
    ("save_provider", "pc", {"models": None}),
    ("save_provider", "pc", {"models": ["invalid"]}),
    ("save_model", "missing", {"provider_id": "missing", "model": "abc"}),
    ("set_default", "missing", None),
    ("delete_provider", "missing", None),
    ("delete_model", "missing", None),
])
def test_invalid_mutations_leave_file_untouched(action, identifier, data):
    update_catalog("save_provider", "pc", provider())
    before = Path(ConfigPaths.JUDGMENT).read_bytes()
    with pytest.raises(ValueError):
        update_catalog(action, identifier, data)
    assert Path(ConfigPaths.JUDGMENT).read_bytes() == before


def test_removing_default_from_provider_does_not_select_another():
    update_catalog("save_provider", "pc", provider())
    update_catalog("set_default", "local")
    update_catalog("save_provider", "pc", {"models": {"other": {"model": "other"}}})
    assert load_catalog().default_model == ""


def test_corrupt_catalog_cannot_be_overwritten():
    path = Path(ConfigPaths.JUDGMENT)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        update_catalog("save_provider", "pc", provider())
    assert path.read_text(encoding="utf-8") == "{broken"


def test_concurrent_provider_updates_do_not_lose_siblings():
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: update_catalog("save_provider", f"p{index}", provider(models={})), range(8)))
    assert len(load_catalog().providers) == 8


async def test_ai_crud_returns_masked_directory_and_structured_errors():
    saved = json.loads(await configure_judgment("save_provider", "pc", provider(api_key="private-judgment-test-key")))
    assert "private-judgment-test-key" not in json.dumps(saved)
    await configure_judgment("save_model", "another", {"provider_id": "pc", "model": "second"})
    await configure_judgment("set_default", "another")
    listed = json.loads(await list_judgment_models())
    assert listed["default_model"] == "another" and "health" in listed
    await configure_judgment("delete_model", "another")
    await configure_judgment("delete_provider", "pc")
    assert not load_catalog().providers
    error = json.loads(await configure_judgment("set_default", "unknown"))
    assert error["cause"] == "config" and error["error"]


@pytest.mark.parametrize("old", [
    {}, {"judgment_api_key": "${OLD_JEV_KEY}", "judgment_model": "jev-latest"},
    {"judgment_base_url": "http://localhost:8090", "judgment_model": "StartLux-Decision-27B"},
])
def test_migration_is_once_only_and_preserves_raw_references(tmp_path, monkeypatch, old):
    source = tmp_path / "old.json"
    source.write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(ConfigPaths, "APP_CONFIG", str(source))
    migrate_judgment_config()
    catalog = load_catalog()
    if old:
        assert catalog.default_model == "default"
        assert catalog.providers["default"].api_key == old.get("judgment_api_key", "")
        update_catalog("delete_provider", "default")
    else:
        assert not catalog.providers
    migrate_judgment_config()
    assert not load_catalog().providers
    assert json.loads(source.read_text(encoding="utf-8")) == old


def test_migration_preserves_environment_key_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(ConfigPaths, "APP_CONFIG", str(tmp_path / "absent.json"))
    monkeypatch.setenv("ANELF_JUDGMENT_API_KEY", "test-environment-key")
    migrate_judgment_config()
    assert load_config().api_key == "test-environment-key"
    assert load_catalog().providers["default"].api_key == "${ANELF_JUDGMENT_API_KEY}"
