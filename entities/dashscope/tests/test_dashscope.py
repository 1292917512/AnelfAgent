"""百炼语音组件测试：注册/密钥解析/流式桥接/音色管理（假 SDK 模块）。"""

from __future__ import annotations

import sys
import types

import pytest

from entities import dashscope as pkg
from entities._sdk import ProviderUnavailable


@pytest.fixture(autouse=True)
def _clean_registries(monkeypatch):
    from agent.audio import get_audio_registry
    from agent.tts import get_tts_registry

    saved_audio = get_audio_registry().list()
    saved_tts = get_tts_registry().list()
    get_audio_registry().reset()
    get_tts_registry().reset()
    from agent.audio.capabilities import reset_sound_router
    reset_sound_router()
    pkg.register_components()
    yield
    get_audio_registry().reset()
    get_tts_registry().reset()
    reset_sound_router()
    for p in saved_audio:
        get_audio_registry().register(p)
    for p in saved_tts:
        get_tts_registry().register(p)


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch):
    """注入伪 dashscope SDK（回调可编程触发）。"""
    calls = {"synth": [], "enroll": [], "recognition": []}

    module = types.ModuleType("dashscope")
    module.api_key = ""

    tts_v2 = types.ModuleType("dashscope.audio.tts_v2")

    class AudioFormat:
        PCM_24000HZ_MONO_16BIT = "pcm_24000"
        PCM_16000HZ_MONO_16BIT = "pcm_16000"

    class SpeechSynthesizer:
        def __init__(self, model="", voice="", format=None, callback=None, **kw):
            self.model, self.voice, self.callback = model, voice, callback
            calls["synth"].append({"model": model, "voice": voice})

        def call(self, text):
            calls["synth"].append({"op": "call", "text": text})
            return b"mp3-bytes"

        def streaming_call(self, text):
            calls["synth"].append({"op": "streaming_call", "text": text})
            self.callback.on_data(b"\x01\x02")

        def streaming_complete(self):
            self.callback.on_data(b"\x03\x04")
            self.callback.on_complete()

    class ResultCallback:
        pass

    tts_v2.AudioFormat = AudioFormat
    tts_v2.SpeechSynthesizer = SpeechSynthesizer
    tts_v2.ResultCallback = ResultCallback

    enrollment = types.ModuleType("dashscope.audio.tts_v2.enrollment")

    class VoiceEnrollmentService:
        def create_voice(self, target_model, prefix, url, **kw):
            calls["enroll"].append(("create", target_model, prefix, url))
            return f"{prefix}-xyz123"

        def list_voices(self, prefix=None, page_index=0, page_size=10):
            calls["enroll"].append(("list", prefix, page_index, page_size))
            return [{"voice_id": "anelf-xyz123", "status": "OK", "gmt_create": "2026"}]

        def delete_voice(self, voice_id):
            calls["enroll"].append(("delete", voice_id))

    enrollment.VoiceEnrollmentService = VoiceEnrollmentService

    asr_mod = types.ModuleType("dashscope.audio.asr")

    class RecognitionResult:
        @staticmethod
        def is_sentence_end(sentence):
            return sentence.get("end_time") is not None

    class Recognition:
        def __init__(self, model="", format="", sample_rate=16000, callback=None):
            self.callback = callback
            calls["recognition"].append(
                {"model": model, "format": format, "rate": sample_rate})

        def call(self, path):
            class R:
                status_code = 200
                def get_sentence(self):
                    return [{"begin_time": 0, "end_time": 900, "text": "你好"}]
            return R()

        def start(self):
            calls["recognition"].append({"op": "start"})

        def send_audio_frame(self, buf):
            calls["recognition"].append({"op": "send", "n": len(buf)})
            self.callback.on_event(type("S", (), {
                "get_sentence": lambda self: {
                    "text": "你好世界", "begin_time": 0, "end_time": None}})())

        def stop(self):
            calls["recognition"].append({"op": "stop"})
            self.callback.on_event(type("S", (), {
                "get_sentence": lambda self: {
                    "text": "你好世界", "begin_time": 0, "end_time": 800}})())

    asr_mod.Recognition = Recognition
    asr_mod.RecognitionResult = RecognitionResult

    audio_mod = types.ModuleType("dashscope.audio")
    for m in (tts_v2, enrollment, asr_mod):
        sys.modules[m.__name__] = m
    sys.modules["dashscope"] = module
    sys.modules["dashscope.audio"] = audio_mod
    sys.modules["dashscope.audio.tts_v2"] = tts_v2
    sys.modules["dashscope.audio.tts_v2.enrollment"] = enrollment
    sys.modules["dashscope.audio.asr"] = asr_mod
    yield calls
    for name in list(sys.modules):
        if name.startswith("dashscope"):
            sys.modules.pop(name, None)


@pytest.fixture
def api_key(monkeypatch: pytest.MonkeyPatch):

    monkeypatch.setattr(pkg.sdk, "resolve_api_key", lambda: "sk-test")
    yield


class TestRegistration:
    async def test_providers_registered(self, fake_sdk, api_key) -> None:
        from agent.audio import get_audio_registry
        from agent.tts import get_tts_registry

        names_asr = [p.name for p in get_audio_registry().list("asr")]
        names_stream = [p.name for p in get_audio_registry().list("asr_stream")]
        tts_names = [p.name for p in get_tts_registry().list()]
        assert "dashscope" in names_asr
        assert "dashscope" in names_stream
        assert "dashscope" in tts_names

    async def test_unavailable_without_key(self, monkeypatch, fake_sdk) -> None:
        monkeypatch.setattr(pkg.sdk, "resolve_api_key", lambda: "")
        from agent.audio import get_audio_registry
        provider = [p for p in get_audio_registry().list("asr")
                    if p.name == "dashscope"][0]
        assert await provider.check_available() is False


class TestTts:
    async def test_stream_synthesize_yields_pcm(self, fake_sdk, api_key) -> None:
        from agent.tts import get_tts_registry

        provider = [p for p in get_tts_registry().list()
                    if p.name == "dashscope"][0]
        stream = provider.stream_synthesize("你好")
        chunks = [c async for c in stream.chunks]
        assert chunks == [b"\x01\x02", b"\x03\x04"]
        assert stream.sample_rate == 24000

    async def test_voice_falls_back_to_default(self, fake_sdk, api_key) -> None:
        from agent.tts import get_tts_registry

        provider = [p for p in get_tts_registry().list()
                    if p.name == "dashscope"][0]
        stream = provider.stream_synthesize("你好", voice="")
        _ = [c async for c in stream.chunks]
        assert fake_sdk["synth"][0]["voice"] == "longanhuan_v3.6"


class TestAsr:
    async def test_stream_session_events(self, fake_sdk, api_key) -> None:
        from agent.audio import get_audio_registry

        provider = [p for p in get_audio_registry().list("asr_stream")
                    if p.name == "dashscope"][0]
        session = provider.open_session(16000)
        events = await session.accept_pcm(b"\x00" * 3200, 16000)
        # 假实现 send 即回调 partial
        assert any(e.kind == "partial" and e.text == "你好世界" for e in events)
        finals = await session.close()
        assert any(e.kind == "final" and e.text == "你好世界" for e in finals)

    async def test_nonstream_segments_shape(
            self, fake_sdk, api_key, tmp_path, monkeypatch) -> None:
        from agent.audio import get_audio_registry

        async def _passthrough(path: str):
            return path, False

        import entities._sdk as sdk_bridge
        monkeypatch.setattr(sdk_bridge, "ensure_16k_mono_wav", _passthrough)

        wav = tmp_path / "a.wav"
        import wave
        with wave.open(str(wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * 3200)
        provider = [p for p in get_audio_registry().list("asr")
                    if p.name == "dashscope"][0]
        segments = await provider.transcribe(str(wav))
        assert segments == [{"start_ms": 0, "end_ms": 900, "text": "你好"}]


class TestVoiceMgmt:
    async def test_clone_with_source_url(self, fake_sdk, api_key) -> None:
        provider = pkg.voice.DashScopeSoundProvider()
        out = await provider.run(
            "voice_mgmt", op="clone", source_url="https://x/a.mp3",
            voice_id="mengli", resolved="/tmp/a.mp3", preview_text="")
        assert out["success"] and out["voice_id"] == "mengli-xyz123"
        assert fake_sdk["enroll"][0][0] == "create"
        assert fake_sdk["enroll"][0][2] == "mengli"  # prefix 规范化（≤10 小写）

    async def test_clone_local_requires_upload_endpoint(
            self, fake_sdk, api_key, monkeypatch) -> None:
        from core.config import ConfigManager

        ConfigManager.set("dashscope_clone_upload_url", "")
        provider = pkg.voice.DashScopeSoundProvider()
        with pytest.raises(ProviderUnavailable):
            await provider.run(
                "voice_mgmt", op="clone", source_url="",
                voice_id="v", resolved="/tmp/a.mp3")

    async def test_list_and_delete(self, fake_sdk, api_key) -> None:
        provider = pkg.voice.DashScopeSoundProvider()
        out = await provider.run("voice_mgmt", op="list")
        assert out["success"] and out["voice_cloning"][0]["voice_id"] == "anelf-xyz123"
        out = await provider.run("voice_mgmt", op="delete", voice_id="anelf-xyz123")
        assert out["deleted"] == "anelf-xyz123"
