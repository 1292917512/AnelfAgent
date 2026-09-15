"""声音生成工具与声音能力路由（agent.audio.gen_tools / capabilities）单元测试。"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

import agent.audio.gen_tools as gen_tools
from agent.audio.capabilities import get_sound_router, reset_sound_router
from agent.audio.gen_tools import sound_config, text_to_voice, voice_to_text


@pytest.fixture(autouse=True)
def _reset_router():
    reset_sound_router()
    yield
    reset_sound_router()


@pytest.fixture
def mem_config(monkeypatch: pytest.MonkeyPatch):
    """内存态配置隔离。"""
    from core.config import ConfigManager
    store: Dict[str, Any] = {}
    monkeypatch.setattr(ConfigManager, "get", staticmethod(lambda k, d=None: store.get(k, d)))
    monkeypatch.setattr(ConfigManager, "set", staticmethod(lambda k, v: store.__setitem__(k, v)))
    monkeypatch.setattr(ConfigManager, "has", staticmethod(lambda k: k in store))
    monkeypatch.setattr(ConfigManager, "save", staticmethod(lambda: True))
    return store


class _FakeProvider:
    def __init__(self, name: str, caps: frozenset, result: Dict[str, Any] | None = None) -> None:
        self.name = name
        self.capabilities = caps
        self._result = result or {"audio_bytes": b"fake-mp3"}
        self.calls: list = []

    def is_configured(self, capability: str) -> bool:
        return True

    def status_details(self, capability: str) -> Dict[str, Any]:
        return {}

    async def run(self, capability: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append((capability, kwargs))
        return dict(self._result)


class TestTextToVoice:
    async def test_default_voice_from_config(
            self, mem_config, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """不传 voice/参考音频时使用默认音色配置。"""
        mem_config["tts_default_voice"] = "female-yujie"
        mem_config["sound_provider_priority"] = {"tts": ["fake"]}
        provider = _FakeProvider("fake", frozenset({"tts"}))
        router = get_sound_router()
        router.register(provider)
        monkeypatch.setattr(gen_tools.ws, "save_audio", lambda b, **kw: "workspace/uploads/audio/gen_1.mp3")

        out = json.loads(await text_to_voice(text="你好"))
        assert out["success"] is True
        assert out["file_path"] == "workspace/uploads/audio/gen_1.mp3"
        assert provider.calls[0][1]["voice"] == "female-yujie"

    async def test_clone_requires_reference_text(self, mem_config) -> None:
        out = json.loads(await text_to_voice(text="你好", reference_audio="https://a.com/x.mp3"))
        assert "error" in out
        assert "reference_text" in out["error"]

    async def test_unknown_provider_rejected(self) -> None:
        get_sound_router()  # 初始化路由（含内部 models 提供者）
        out = json.loads(await text_to_voice(text="你好", provider="nope"))
        assert "error" in out
        assert out.get("cause") == "param"


class TestVoiceToText:
    async def test_missing_source_param_error(self) -> None:
        out = json.loads(await voice_to_text())
        assert "error" in out
        assert out.get("cause") == "param"

    async def test_local_file_transcribed(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        audio = tmp_path / "a.ogg"
        audio.write_bytes(b"fake")
        monkeypatch.setattr(gen_tools.ws, "resolve_workspace_path", lambda p: str(audio))

        class _Svc:
            async def transcribe(self, path: str) -> list:
                return [{"text": "你好"}, {"text": "世界"}]

        monkeypatch.setattr(gen_tools, "_svc_resolver", None, raising=False)
        import agent.audio.service as svc_mod
        monkeypatch.setattr(svc_mod, "get_audio_service", lambda: _Svc())

        out = json.loads(await voice_to_text(audio_source="workspace/a.ogg"))
        assert out["success"] is True
        assert out["text"] == "你好\n世界"
        assert out["segments"] == 2

    async def test_no_asr_provider_config_error(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        audio = tmp_path / "a.ogg"
        audio.write_bytes(b"fake")
        monkeypatch.setattr(gen_tools.ws, "resolve_workspace_path", lambda p: str(audio))

        from agent.audio.service import AudioNotConfigured

        class _Svc:
            async def transcribe(self, path: str) -> list:
                raise AudioNotConfigured("无可用 ASR 提供者")

        import agent.audio.service as svc_mod
        monkeypatch.setattr(svc_mod, "get_audio_service", lambda: _Svc())

        out = json.loads(await voice_to_text(audio_source="workspace/a.ogg"))
        assert "error" in out
        assert out.get("cause") == "config"


class TestSoundConfig:
    async def test_capabilities_matrix(self, mem_config) -> None:
        get_sound_router().register(_FakeProvider("fake", frozenset({"tts"})))
        out = json.loads(await sound_config("capabilities"))
        assert out["success"] is True
        assert "tts" in out["capabilities"]
        assert "asr" in out["capabilities"]

    async def test_set_default_voice(self, mem_config) -> None:
        out = json.loads(await sound_config("set", "default_voice", "v1"))
        assert out["success"] is True
        assert mem_config["tts_default_voice"] == "v1"

    async def test_set_provider_priority(self, mem_config) -> None:
        get_sound_router().register(_FakeProvider("fake", frozenset({"tts"})))
        out = json.loads(await sound_config("set", "provider_priority.tts", '["fake","models"]'))
        assert out["success"] is True
        assert mem_config["sound_provider_priority"] == {"tts": ["fake", "models"]}
        assert out["chain"] == ["fake", "models"]

    async def test_set_unknown_key_rejected(self, mem_config) -> None:
        out = json.loads(await sound_config("set", "bad_key", "x"))
        assert "error" in out
        assert out.get("cause") == "param"

    async def test_get_config(self, mem_config) -> None:
        out = json.loads(await sound_config("get"))
        assert out["success"] is True
        assert "default_voice" in out["config"]


class TestSoundConfigFunasr:
    @pytest.fixture(autouse=True)
    def _funasr_cred(self, tmp_path, monkeypatch):
        """FunASR 凭据写入隔离的凭据中心存储（不碰真凭据文件）。"""
        from core import provider_keys as pk

        monkeypatch.setattr(pk, "_path", lambda: str(tmp_path / "keys.json"))
        monkeypatch.setattr(pk, "_cache", None)

    async def test_get_includes_funasr(self, monkeypatch) -> None:
        from core import provider_keys as pk
        from entities.audiosync import client as funasr_client

        async def fake_probe() -> bool:
            return True

        monkeypatch.setattr(funasr_client, "probe_available", fake_probe)
        monkeypatch.setattr(funasr_client, "reset_probe_cache", lambda: None)
        pk.set_provider_key("funasr", "funasr_endpoint", "http://funasr.local")
        out = json.loads(await sound_config(action="get"))
        assert out["config"]["funasr_endpoint"] == "http://funasr.local"
        assert out["config"]["funasr_reachable"] is True

    async def test_set_funasr_endpoint_reports_reachability(self, monkeypatch) -> None:
        from entities.audiosync import client as funasr_client

        async def fake_probe() -> bool:
            return False

        monkeypatch.setattr(funasr_client, "probe_available", fake_probe)
        monkeypatch.setattr(funasr_client, "reset_probe_cache", lambda: None)
        out = json.loads(await sound_config(action="set", key="funasr_endpoint",
                                            value="http://funasr.local"))
        assert out["success"] is True
        assert out["reachable"] is False
        assert "不可达" in out["hint"]
