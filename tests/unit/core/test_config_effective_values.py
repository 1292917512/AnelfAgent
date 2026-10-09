import json
from pathlib import Path

import pytest

from core.config import ConfigManager


def test_saved_values_keep_environment_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setattr(ConfigManager, "_config_file", str(path))
    monkeypatch.setattr(ConfigManager, "_file_config", {"audit_limit": 10})
    monkeypatch.setattr(ConfigManager, "_config", {"audit_limit": 10})
    monkeypatch.setattr(ConfigManager, "_stores", {})
    monkeypatch.setattr(ConfigManager, "_listeners", {})
    monkeypatch.setenv("ANELF_AUDIT_LIMIT", "20")
    seen: list[object] = []
    ConfigManager.add_listener("audit_", lambda key, value: seen.append(value))
    ConfigManager.set_persisted({"audit_limit": 30})
    assert ConfigManager.get("audit_limit") == 20
    assert seen == [20]
    assert json.loads(path.read_text())["audit_limit"] == 30
    ConfigManager.reload()
    assert ConfigManager.get("audit_limit") == 20
    ConfigManager.update({"audit_limit": 40})
    assert ConfigManager.get("audit_limit") == 20
    assert ConfigManager._file_config["audit_limit"] == 40


def test_reference_is_preserved_on_disk_and_resolved_in_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setattr(ConfigManager, "_config_file", str(path))
    monkeypatch.setattr(ConfigManager, "_file_config", {})
    monkeypatch.setattr(ConfigManager, "_config", {})
    monkeypatch.setattr(ConfigManager, "_stores", {})
    monkeypatch.setattr(ConfigManager, "_listeners", {})
    monkeypatch.setenv("AUDIT_SECRET", "test-value")
    ConfigManager.set_persisted({"secret": "${AUDIT_SECRET}"})
    assert ConfigManager.get("secret") == "test-value"
    assert json.loads(path.read_text())["secret"] == "${AUDIT_SECRET}"
