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


@pytest.fixture
def app(monkeypatch):
    fake = FakeApp()
    monkeypatch.setattr(
        "agent.runtime.agent_app.get_agent_app", lambda: fake, raising=False)
    return fake
