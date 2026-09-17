"""音色解析测试：链序（显式 → 场景覆盖 → 全局默认 → 协议音色）。"""

from __future__ import annotations

import pytest

from agent.tts.voice import default_voice, realtime_voice, resolve_voice
from core.config import ConfigManager


@pytest.fixture(autouse=True)
def voice_config():
    saved = (ConfigManager.get("tts_default_voice", ""), ConfigManager.get("realtime_tts_voice", ""))
    ConfigManager.set("tts_default_voice", "")
    ConfigManager.set("realtime_tts_voice", "")
    yield
    ConfigManager.set("tts_default_voice", saved[0])
    ConfigManager.set("realtime_tts_voice", saved[1])


class TestVoiceResolution:
    def test_no_config_falls_to_protocol_voice(self) -> None:
        assert default_voice() == ""
        assert realtime_voice() == ""
        assert resolve_voice("", "alloy") == "alloy"

    def test_default_voice_used_by_all_entries(self) -> None:
        ConfigManager.set("tts_default_voice", "male-qn-qingse")
        assert default_voice() == "male-qn-qingse"
        assert realtime_voice() == "male-qn-qingse"
        assert resolve_voice("", "alloy") == "male-qn-qingse"

    def test_realtime_override_beats_default(self) -> None:
        ConfigManager.set("tts_default_voice", "male-qn-qingse")
        ConfigManager.set("realtime_tts_voice", "qiaopi_mengmei")
        assert realtime_voice() == "qiaopi_mengmei"
        assert default_voice() == "male-qn-qingse"

    def test_explicit_voice_beats_everything(self) -> None:
        ConfigManager.set("tts_default_voice", "male-qn-qingse")
        assert resolve_voice("custom-voice", "alloy") == "custom-voice"

    def test_blank_strings_treated_as_unset(self) -> None:
        ConfigManager.set("realtime_tts_voice", "  ")
        ConfigManager.set("tts_default_voice", " vo-a ")
        assert realtime_voice() == "vo-a"
