from __future__ import annotations

import dataclasses
import json

import pytest

from agent.config import (
    MIND_CONFIG_FIELDS,
    BotConfig,
    BotConfigProvider,
    MindConfig,
)


def test_mind_config_fields_cover_dataclass() -> None:
    assert set(MIND_CONFIG_FIELDS) == {f.name for f in dataclasses.fields(MindConfig)}


def test_save_mind_config_preserves_all_supported_fields(tmp_path) -> None:
    class TestProvider(BotConfigProvider):
        @property
        def mind_config_path(self) -> str:
            return str(tmp_path / "mind.json")

    provider = object.__new__(TestProvider)
    provider._config = BotConfig()
    provider._cm_available = False

    provider.save_mind_config(
        llm_timeout=45, send_interim_text=True, send_final_text=False,
        autonomous_decision_interval=7200,
    )

    data = json.loads((tmp_path / "mind.json").read_text(encoding="utf-8"))
    assert set(MIND_CONFIG_FIELDS).issubset(data)
    assert data["llm_timeout"] == 45
    assert data["send_interim_text"] is True
    assert data["send_final_text"] is False
    assert data["autonomous_decision_interval"] == 7200.0
    assert data["heartbeat_interval"] == 300.0
    assert "tool_system_rules" in data


def test_existing_mind_config_uses_hourly_decisions_without_changing_heartbeat(tmp_path) -> None:
    class TestProvider(BotConfigProvider):
        @property
        def mind_config_path(self) -> str:
            return str(tmp_path / "mind.json")

    path = tmp_path / "mind.json"
    path.write_text(json.dumps({"heartbeat_interval": 600.0}), encoding="utf-8")
    provider = object.__new__(TestProvider)
    provider._config = BotConfig()
    provider._cm_available = False
    provider._load_mind_config()

    assert provider.mind.autonomous_decision_interval == 3600.0
    assert provider.mind.heartbeat_interval == 600.0
    assert json.loads(path.read_text(encoding="utf-8")) == {"heartbeat_interval": 600.0}


def test_save_mind_config_rejects_unknown_field(tmp_path) -> None:
    class TestProvider(BotConfigProvider):
        @property
        def mind_config_path(self) -> str:
            return str(tmp_path / "mind.json")

    provider = object.__new__(TestProvider)
    provider._config = BotConfig()
    provider._cm_available = False

    with pytest.raises(ValueError, match="未知 Mind 配置字段"):
        provider.save_mind_config(reasoning_effort="high")
