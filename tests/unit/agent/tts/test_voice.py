"""音色解析测试：场景指派 → 预设内容 → 提供者协议音色的决策链。"""

from __future__ import annotations

import pytest

from agent.tts import presets
from agent.tts.voice import (
    default_preset,
    default_voice,
    realtime_preset,
    realtime_voice,
    resolve_voice,
)
from core.config import ConfigManager


@pytest.fixture
def voice_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """预设库与指派键隔离到临时域。"""
    monkeypatch.setattr(presets, "_store_path", lambda: str(tmp_path / "voice_presets.json"))
    store: dict = {}
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: store.get(k, d)))
    monkeypatch.setattr(ConfigManager, "set", staticmethod(lambda k, v: store.__setitem__(k, v)))
    monkeypatch.setattr(ConfigManager, "save", staticmethod(lambda: True))
    return store


class TestVoiceResolution:
    def test_no_assignment_falls_to_protocol(self, voice_env) -> None:
        assert default_preset() is None
        assert default_voice() == ""
        assert realtime_voice() == ""
        assert resolve_voice("", "alloy") == "alloy"

    def test_default_preset_serves_all_entries(self, voice_env) -> None:
        preset = presets.save_preset(name="御姐", voice_id="female-yujie")
        presets.assign_voice("default", preset.id)
        assert default_voice() == "female-yujie"
        assert realtime_voice() == "female-yujie"
        assert resolve_voice("", "alloy") == "female-yujie"

    def test_realtime_preset_overrides_default(self, voice_env) -> None:
        base = presets.save_preset(name="日常", voice_id="female-yujie")
        call = presets.save_preset(name="通话", voice_id="qiaopi_mengmei")
        presets.assign_voice("default", base.id)
        presets.assign_voice("realtime", call.id)
        assert realtime_preset() == call
        assert realtime_voice() == "qiaopi_mengmei"
        assert default_voice() == "female-yujie"

    def test_clone_preset_not_streamable(self, voice_env) -> None:
        """克隆型默认预设：一次性合成走参考对，流式（通话）落协议音色。"""
        preset = presets.save_preset(
            name="克隆音", reference_audio="workspace/uploads/a.mp3", reference_text="你好")
        presets.assign_voice("default", preset.id)
        assert default_preset() == preset
        assert preset.reference_audio.endswith("a.mp3")
        assert default_voice() == ""
        assert realtime_voice() == ""
        assert resolve_voice("", "alloy") == "alloy"

    def test_explicit_voice_beats_preset(self, voice_env) -> None:
        preset = presets.save_preset(name="御姐", voice_id="female-yujie")
        presets.assign_voice("default", preset.id)
        assert resolve_voice("custom-voice", "alloy") == "custom-voice"
