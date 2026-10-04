"""realtime 测试共享夹具（实现件在 rt_fakes.py，命名防 conftest 遮蔽）。"""

from __future__ import annotations

import pytest
from rt_fakes import FakeApp, FakeStreamAsrProvider, FakeTtsProvider


@pytest.fixture
def clean_registries():
    from agent.audio import get_audio_registry
    from agent.tts import get_tts_registry
    saved_audio = get_audio_registry().list()
    saved_tts = get_tts_registry().list()
    get_audio_registry().reset()
    get_tts_registry().reset()
    get_audio_registry().register(FakeStreamAsrProvider())
    get_tts_registry().register(FakeTtsProvider())
    yield
    get_audio_registry().reset()
    get_tts_registry().reset()
    for p in saved_audio:
        get_audio_registry().register(p)
    for p in saved_tts:
        get_tts_registry().register(p)


@pytest.fixture(autouse=True)
def energy_detector():
    """端点检测钉在能量法：引擎用例喂合成音（非真语音，模型级 VAD 不认），
    不随宿主机是否装了 onnxruntime/下载了模型而漂移。"""
    from core.config import ConfigManager

    ConfigManager.set("voice_turn_detector", "energy")
    yield
    ConfigManager.set("voice_turn_detector", "auto")


@pytest.fixture(autouse=True)
def no_recording_retention():
    """轮次录音留存默认关：测试不落真实 WAV 到 workspace。"""
    from core.config import ConfigManager

    ConfigManager.set("realtime_keep_recordings", False)
    yield
    ConfigManager.set("realtime_keep_recordings", True)


@pytest.fixture(autouse=True)
def ingest_spy(monkeypatch):
    """入库替身：实时引擎的轮末 ingest 不触真实音频库（SQLite/声纹），
    仅记录载荷供断言（测试隔离；需要断言入库行为时读本 fixture 的 calls）。"""
    from agent.audio import service as audio_service_mod
    from agent.audio.schemas import IngestResult

    calls: list = []

    async def _spy(self, payload, *, store=None):
        calls.append(payload)
        return IngestResult(ingested=len(payload.segments), skipped=0, results=[])

    monkeypatch.setattr(audio_service_mod.AudioService, "ingest_payload", _spy)
    yield calls


@pytest.fixture
def app(monkeypatch):
    fake = FakeApp()
    monkeypatch.setattr(
        "agent.runtime.agent_app.get_agent_app", lambda: fake, raising=False)
    return fake
