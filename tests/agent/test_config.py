from __future__ import annotations

import json

from agent.config import (
    BotConfig,
    BotConfigProvider,
    _MIND_SYNC_FIELDS,
)


def test_save_mind_config_preserves_all_supported_fields(tmp_path) -> None:
    class TestProvider(BotConfigProvider):
        @property
        def mind_config_path(self) -> str:
            return str(tmp_path / "mind.json")

    provider = object.__new__(TestProvider)
    provider._config = BotConfig()
    provider._cm_available = False

    provider.save_mind_config(llm_timeout=45, send_interim_text=True)

    data = json.loads((tmp_path / "mind.json").read_text(encoding="utf-8"))
    assert set(_MIND_SYNC_FIELDS).issubset(data)
    assert data["llm_timeout"] == 45
    assert data["send_interim_text"] is True
    assert "tool_system_rules" in data
