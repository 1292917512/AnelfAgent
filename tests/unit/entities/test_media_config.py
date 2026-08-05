"""entities/media media_config 工具与 config.update_key 单元测试。"""

from __future__ import annotations

import json

import pytest

import entities.media.config as media_config_mod
import entities.media.tools as mtools


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """配置指向临时文件并重置缓存。"""
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "default_voice": "",
        "provider_priority": {"music": ["models", "minimax"]},
        "style_presets": {"nekomimi_maid": "anime style, cat ears"},
    }), encoding="utf-8")
    monkeypatch.setattr(media_config_mod, "_CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(media_config_mod, "_cache", None)


class TestUpdateKey:
    def test_scalar(self):
        saved = media_config_mod.update_key("default_voice", "my-voice-01")
        assert saved["default_voice"] == "my-voice-01"
        assert media_config_mod.load_config()["default_voice"] == "my-voice-01"

    def test_defaults_int_coercion(self):
        saved = media_config_mod.update_key("defaults.video_duration", "10")
        assert saved["defaults"]["video_duration"] == 10

    def test_style_preset_add_and_delete(self):
        saved = media_config_mod.update_key("style_presets.cyberpunk", "cyberpunk, neon lights")
        assert saved["style_presets"]["cyberpunk"] == "cyberpunk, neon lights"
        saved = media_config_mod.update_key("style_presets.cyberpunk", "")
        assert "cyberpunk" not in saved["style_presets"]
        assert saved["style_presets"]["nekomimi_maid"] == "anime style, cat ears"

    def test_provider_priority(self):
        saved = media_config_mod.update_key("provider_priority.tts", ["minimax", "models"])
        assert saved["provider_priority"]["tts"] == ["minimax", "models"]
        assert media_config_mod.provider_chain("tts") == ["minimax", "models"]

    def test_unknown_key(self):
        with pytest.raises(ValueError, match="不支持的配置键"):
            media_config_mod.update_key("hacked.key", "x")

    def test_unknown_defaults_key(self):
        with pytest.raises(ValueError, match="不支持的默认参数键"):
            media_config_mod.update_key("defaults.nope", "x")


class TestMediaConfigTool:
    async def test_get(self):
        out = json.loads(await mtools.media_config("get"))
        assert out["success"] is True
        assert "provider_priority" in out["config"]
        assert out["config"]["style_presets"]["nekomimi_maid"] == "anime style, cat ears"

    async def test_set_default_voice(self):
        out = json.loads(await mtools.media_config("set", "default_voice", "my-voice-01"))
        assert out["success"] is True
        assert out["value"] == "my-voice-01"
        assert "hint" in out

    async def test_set_style_preset(self):
        out = json.loads(await mtools.media_config("set", "style_presets.cyberpunk", "cyberpunk, neon"))
        assert out["success"] is True
        assert media_config_mod.load_config()["style_presets"]["cyberpunk"] == "cyberpunk, neon"

    async def test_set_provider_priority_json(self):
        out = json.loads(await mtools.media_config("set", "provider_priority.tts", '["minimax","models"]'))
        assert out["success"] is True
        assert media_config_mod.provider_chain("tts") == ["minimax", "models"]

    async def test_set_provider_priority_csv(self):
        out = json.loads(await mtools.media_config("set", "provider_priority.vision", "minimax,models"))
        assert out["success"] is True
        assert media_config_mod.provider_chain("vision") == ["minimax", "models"]

    async def test_invalid_capability(self):
        out = json.loads(await mtools.media_config("set", "provider_priority.nope", '["models"]'))
        assert out.get("cause") == "param"

    async def test_invalid_provider_name(self):
        out = json.loads(await mtools.media_config("set", "provider_priority.tts", '["unknown"]'))
        assert out.get("cause") == "param"

    async def test_missing_key(self):
        out = json.loads(await mtools.media_config("set"))
        assert out.get("cause") == "param"

    async def test_unknown_action(self):
        out = json.loads(await mtools.media_config("delete", "default_voice"))
        assert out.get("cause") == "param"


class TestMediaConfigStatus:
    @pytest.fixture(autouse=True)
    def _stub_provider_state(self, monkeypatch: pytest.MonkeyPatch):
        """屏蔽真实 LLMManager/MiniMax 客户端，固定 provider 配置状态。"""
        import entities.media.providers as providers_mod
        monkeypatch.setattr(providers_mod._PROVIDERS["models"], "is_configured", lambda cap: True)
        monkeypatch.setattr(providers_mod._PROVIDERS["minimax"], "is_configured", lambda cap: False)

    async def test_providers_action(self):
        out = json.loads(await mtools.media_config("providers"))
        assert out["success"] is True
        names = {p["name"] for p in out["providers"]}
        assert names == {"models", "minimax"}
        assert "provider_priority" in out

    async def test_capabilities_action(self):
        out = json.loads(await mtools.media_config("capabilities"))
        assert out["success"] is True
        caps = out["capabilities"]
        # 覆盖全部能力，且含指南与实时状态
        assert set(caps) == {
            "vision", "asr", "tts", "voice_mgmt", "music",
            "video", "image_gen", "image_edit", "rerank",
        }
        vision = caps["vision"]
        assert vision["available"] is True  # models 已配置
        assert vision["tools"] == ["recognize_image"]
        assert vision["chain"] == ["models", "minimax"]
        states = {p["name"]: p["configured"] for p in vision["providers"]}
        assert states == {"models": True, "minimax": False}
        assert vision["example"].startswith("recognize_image(")
        # 单 provider 链
        assert caps["asr"]["chain"] == ["models"]
        assert len(caps["asr"]["providers"]) == 1
        # minimax 在链上但不支持该能力时应标注（music 链配置为 ["models","minimax"]）
        music = caps["music"]
        assert music["chain"] == ["models", "minimax"]
        assert music["providers"][1]["note"] == "不支持该能力"
