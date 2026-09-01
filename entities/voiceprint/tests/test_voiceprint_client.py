"""FunASR client 测试：预处理贯通 / 失败回退 / source_time 透传 / 契约解析。"""

from __future__ import annotations

import pytest

import entities.voiceprint.client as client_mod
from core.config import ConfigManager


@pytest.fixture
def funasr_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """配置 endpoint + 造一个假音频文件。"""
    ConfigManager.set("voiceprint_funasr_endpoint", "http://funasr.local")
    audio = tmp_path / "clip.m4a"
    audio.write_bytes(b"fake-audio")
    captured: dict = {}

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"segments": [{
                "start_ms": 0, "end_ms": 1000, "text": "你好",
                "vector": [1.0] + [0.0] * 191,
                "abs_start_ms": 1786005000000, "abs_end_ms": 1786005001000,
            }]}

    class _FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, files=None, data=None):
            captured["url"] = url
            captured["filename"] = files["file"][0] if files else None
            captured["data"] = data
            return _FakeResponse()

    monkeypatch.setattr(client_mod.httpx, "AsyncClient", _FakeAsyncClient)
    return audio, captured


class TestTranscribe:
    async def test_preprocess_and_source_time(self, funasr_env, monkeypatch) -> None:
        audio, captured = funasr_env
        converted_wav = str(audio) + ".wav"

        async def fake_wav(path: str):
            with open(converted_wav, "wb") as f:
                f.write(b"wav-bytes")
            return converted_wav, True

        monkeypatch.setattr(client_mod, "_ensure_wav", fake_wav)
        segments = await client_mod.transcribe(str(audio), source_time="1786005000000")
        assert captured["url"] == "http://funasr.local/transcribe"
        # 转换后按 .wav 文件名上传
        assert captured["filename"] == "clip.wav"
        # source_time 已透传
        assert captured["data"] == {"source_time": "1786005000000"}
        # abs 字段解析
        assert segments[0]["abs_start_ms"] == 1786005000000
        assert segments[0]["vector"] is not None

    async def test_fallback_when_preprocess_fails(self, funasr_env, monkeypatch) -> None:
        audio, captured = funasr_env

        async def fake_wav_fail(path: str):
            raise client_mod.audio_pre.PreprocessError("ffmpeg 不可用")

        monkeypatch.setattr(client_mod, "_ensure_wav", fake_wav_fail)
        segments = await client_mod.transcribe(str(audio))
        # 回退原始文件直传，保留原文件名，不带 source_time
        assert captured["filename"] == "clip.m4a"
        assert captured["data"] is None
        assert len(segments) == 1

    async def test_not_configured(self) -> None:
        ConfigManager.set("voiceprint_funasr_endpoint", "")
        with pytest.raises(client_mod.FunAsrNotConfigured):
            await client_mod.transcribe("/tmp/any.wav")
