"""FunASR client 测试：预处理贯通 / 失败回退 / source_time 透传 / 契约解析。"""

from __future__ import annotations

import pytest

import entities.audiosync.client as client_mod


@pytest.fixture
def funasr_cred(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """FunASR 地址写入隔离的凭据中心存储（不碰真凭据文件）。"""
    from core import provider_keys as pk

    monkeypatch.setattr(pk, "_path", lambda: str(tmp_path / "keys.json"))
    monkeypatch.setattr(pk, "_cache", None)

    def _set(value: str) -> None:
        pk.set_provider_key("funasr", "funasr_endpoint", value)

    return _set


@pytest.fixture
def funasr_env(tmp_path, monkeypatch: pytest.MonkeyPatch, funasr_cred):
    """配置 endpoint + 造一个假音频文件。"""
    funasr_cred("http://funasr.local")
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
            raise client_mod.PreprocessError("ffmpeg 不可用")

        monkeypatch.setattr(client_mod, "_ensure_wav", fake_wav_fail)
        segments = await client_mod.transcribe(str(audio))
        # 回退原始文件直传，保留原文件名，不带 source_time
        assert captured["filename"] == "clip.m4a"
        assert captured["data"] is None
        assert len(segments) == 1

    async def test_not_configured(self, funasr_cred) -> None:
        funasr_cred("")
        with pytest.raises(client_mod.FunAsrNotConfigured):
            await client_mod.transcribe("/tmp/any.wav")


class TestProbe:
    async def test_unconfigured_probes_false_without_network(self, funasr_cred) -> None:
        from entities.audiosync import client as c

        funasr_cred("")
        c.reset_probe_cache()
        assert await c.probe_available() is False

    async def test_probe_result_cached(self, monkeypatch, funasr_cred) -> None:
        from entities.audiosync import client as c

        funasr_cred("http://funasr.local")
        c.reset_probe_cache()
        calls = {"n": 0}

        async def _fake_get(self, url):
            calls["n"] += 1
            raise RuntimeError("unreachable")

        monkeypatch.setattr(c.httpx.AsyncClient, "get", _fake_get)
        assert await c.probe_available() is False
        assert await c.probe_available() is False  # 命中缓存不再发请求
        assert calls["n"] == 1
