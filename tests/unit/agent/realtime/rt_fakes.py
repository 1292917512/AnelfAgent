"""realtime 测试共享假件：ASR/TTS 提供者桩、应用桩、录制 sink、夹具。"""

from __future__ import annotations

import asyncio
import math

import pytest

from agent.realtime.session import RealtimeSink
from agent.tts.providers import TtsStream
from agent.voice.session import VoiceDelivery

RATE = 16000


def pcm_silence(ms: int) -> bytes:
    return b"\x00\x00" * int(RATE * ms / 1000)


def pcm_tone(ms: int, amp: int = 8000) -> bytes:
    n = int(RATE * ms / 1000)
    frames = bytearray()
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * 440 * i / RATE))
        frames += v.to_bytes(2, "little", signed=True)
    return bytes(frames)


class FakeStreamAsrSession:
    def __init__(self) -> None:
        self.pcm = bytearray()

    async def accept_pcm(self, pcm: bytes, sample_rate: int):
        self.pcm.extend(pcm)
        return []

    async def commit(self):
        from agent.audio.streaming import AsrEvent
        return [AsrEvent(kind="final", text="你好世界", segments=[])]

    async def close(self):
        return []


class FakeStreamAsrProvider:
    name = "fake_stream"
    kind = "asr_stream"
    priority = 1

    async def check_available(self) -> bool:
        return True

    def open_session(self, sample_rate: int = 16000):
        return FakeStreamAsrSession()


class FakeTtsProvider:
    name = "fake_tts"
    priority = 1

    async def check_available(self) -> bool:
        return True

    def stream_synthesize(self, text: str, *, voice: str = "", sample_rate: int = 24000):
        async def _chunks():
            yield b"\x01\x02" * 240
        return TtsStream(_chunks(), 24000)


class FakeApp:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


class FakeSink:
    def __init__(self) -> None:
        self.audio: list[tuple[bytes, int]] = []
        self.events: list[tuple[str, dict]] = []

    def as_sink(self) -> RealtimeSink:
        async def _audio(pcm: bytes, rate: int) -> None:
            self.audio.append((pcm, rate))

        async def _event(name: str, payload: dict) -> None:
            self.events.append((name, payload))

        return RealtimeSink(send_audio=_audio, send_event=_event)


def make_delivery() -> VoiceDelivery:
    return VoiceDelivery(user_id="u1", user_name="用户", session_id="", adapter_key="webui")


async def wait_for(cond, timeout: float = 3.0) -> None:
    async def _poll():
        while not cond():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(_poll(), timeout)


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
