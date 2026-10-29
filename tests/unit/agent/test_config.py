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


@pytest.mark.parametrize("limit", [None, 0, 12])
def test_existing_mind_config_preserves_explicit_limits_and_heartbeat(tmp_path, limit: int | None) -> None:
    class TestProvider(BotConfigProvider):
        @property
        def mind_config_path(self) -> str:
            return str(tmp_path / "mind.json")

    path = tmp_path / "mind.json"
    data = {"heartbeat_interval": 600.0}
    if limit is not None:
        data["max_tool_iterations"] = limit
    path.write_text(json.dumps(data), encoding="utf-8")
    provider = object.__new__(TestProvider)
    provider._config = BotConfig()
    provider._cm_available = False
    provider._load_mind_config()

    assert provider.mind.autonomous_decision_interval == 3600.0
    assert provider.mind.heartbeat_interval == 600.0
    assert provider.mind.max_tool_iterations == (limit or 0)
    assert json.loads(path.read_text(encoding="utf-8")) == data


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


@pytest.mark.parametrize(("app_limit", "mind_limit", "expected"), [
    (12, None, 12), (12, 7, 7), (12, 0, 0), (0, None, 0), (0, 12, 12),
])
def test_existing_iteration_configuration_preserves_precedence_on_save_and_reload(
    tmp_path, app_limit: int, mind_limit: int | None, expected: int,
) -> None:
    from core.config import ConfigManager

    class TestProvider(BotConfigProvider):
        @property
        def mind_config_path(self) -> str:
            return str(tmp_path / "mind.json")

    path = tmp_path / "mind.json"
    if mind_limit is not None:
        path.write_text(json.dumps({"max_tool_iterations": mind_limit}), encoding="utf-8")
    ConfigManager.set("max_tool_iterations", app_limit)
    provider = TestProvider()
    assert provider.mind.max_tool_iterations == expected
    assert path.exists() == (mind_limit is not None)
    provider.save_mind_config(send_interim_text=True)
    provider.reload()
    assert provider.mind.max_tool_iterations == expected
    assert json.loads(path.read_text(encoding="utf-8"))["max_tool_iterations"] == expected
