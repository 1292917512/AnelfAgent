"""持久化失败必须保持原内存值且不能提前发布热更新。"""

from pathlib import Path

import pytest

from agent.channel.config import ChannelConfigStore
from core.config import ConfigManager


def test_main_config_failure_does_not_publish(monkeypatch: pytest.MonkeyPatch):
    ConfigManager.set("test", "old")
    seen = []
    ConfigManager.add_listener("test", lambda key, value: seen.append(value))

    def fail(path: Path, content: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("core.file_utils.atomic_write_text", fail)
    with pytest.raises(OSError, match="disk full"):
        ConfigManager.set_persisted({"test": "new"})
    assert ConfigManager.get("test") == "old"
    assert seen == []


def test_external_failure_restores_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "channel.json"
    path.write_text('{"token": "old"}', encoding="utf-8")
    store = ChannelConfigStore("demo", path)
    ConfigManager.register_store("demo_", store)
    seen = []
    ConfigManager.add_listener("demo_", lambda key, value: seen.append(value))

    def fail() -> None:
        raise OSError("read only")

    monkeypatch.setattr(store, "save", fail)
    with pytest.raises(OSError, match="read only"):
        ConfigManager.set_persisted({"demo_token": "new", "demo_new_key": 1})
    assert ConfigManager.get("demo_token") == "old"
    assert not ConfigManager.has("demo_new_key")
    assert seen == []
    assert ConfigManager.save() is False
