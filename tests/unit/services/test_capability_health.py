"""能力面健康探测互不阻塞，超时不隐瞒其他提供者状态。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import audio


async def test_audio_status_probes_start_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    started: set[str] = set()
    released = asyncio.Event()

    async def probe(name: str) -> bool:
        started.add(name)
        if len(started) == 3:
            released.set()
        await released.wait()
        return True

    def provider(name: str, kind: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, kind=kind, priority=1, check_available=lambda: probe(name))

    providers = [provider("asr", "asr"), provider("voiceprint", "voiceprint")]
    monkeypatch.setattr(audio, "get_audio_registry", lambda: SimpleNamespace(list=lambda: providers))
    monkeypatch.setattr("agent.tts.get_tts_registry", lambda: SimpleNamespace(list=lambda: [provider("tts", "tts")]))
    monkeypatch.setattr(audio, "get_audio_store", lambda: SimpleNamespace(stats=AsyncMock(return_value={})))
    result = await asyncio.wait_for(audio.AudioServiceFacade().status(), timeout=2)
    assert started == {"asr", "voiceprint", "tts"}
    assert result["asr_available"] and result["voiceprint_available"] and result["tts_available"]


async def test_probe_timeout_cancels_probe_and_keeps_other_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "_STATUS_PROBE_SECONDS", 0.01)
    cancelled = asyncio.Event()

    async def blocked() -> bool:
        try:
            await asyncio.Event().wait()
            return True
        finally:
            cancelled.set()

    slow = SimpleNamespace(name="slow", priority=1, check_available=blocked)
    ready = SimpleNamespace(name="ready", priority=2, check_available=AsyncMock(return_value=True))
    reports = await asyncio.gather(audio._probe_provider(slow), audio._probe_provider(ready))
    assert reports[0]["state"] == "timeout"
    assert reports[1]["state"] == "ready"
    assert cancelled.is_set()
