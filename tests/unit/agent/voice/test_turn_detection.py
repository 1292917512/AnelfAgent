"""端点检测测试：能量法事件序列 / 工厂降级 / silero 概率判段（mock 推理）。"""

from __future__ import annotations

import math

from agent.voice.turn_detection import (
    EnergyTurnDetector,
    SileroTurnDetector,
    TurnEvent,
    create_turn_detector,
)

RATE = 16000


def pcm_silence(ms: int) -> bytes:
    return b"\x00\x00" * int(RATE * ms / 1000)


def pcm_tone(ms: int, amp: int = 8000) -> bytes:
    """正弦波 PCM16（能量法可识别的稳定语音信号）。"""
    n = int(RATE * ms / 1000)
    frames = bytearray()
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * 440 * i / RATE))
        frames += v.to_bytes(2, "little", signed=True)
    return bytes(frames)


class TestEnergyTurnDetector:
    def test_silence_no_events(self) -> None:
        det = EnergyTurnDetector(RATE)
        events = [det.accept_pcm(pcm_silence(20)) for _ in range(50)]
        assert set(events) == {TurnEvent.NONE}
        assert not det.in_speech

    def test_start_then_end_sequence(self) -> None:
        det = EnergyTurnDetector(RATE)
        events = [det.accept_pcm(pcm_silence(20)) for _ in range(50)]  # 1s 底噪学习
        events += [det.accept_pcm(pcm_tone(20)) for _ in range(50)]   # 1s 语音
        assert TurnEvent.SPEECH_START in events
        assert det.in_speech
        events = [det.accept_pcm(pcm_silence(20)) for _ in range(60)]  # 1.2s 静音
        assert TurnEvent.SPEECH_END in events
        assert not det.in_speech

    def test_brief_noise_no_start(self) -> None:
        det = EnergyTurnDetector(RATE)
        for _ in range(50):
            det.accept_pcm(pcm_silence(20))
        events = [det.accept_pcm(pcm_tone(20)) for _ in range(3)]  # 60ms 突发
        events += [det.accept_pcm(pcm_silence(20)) for _ in range(50)]
        assert TurnEvent.SPEECH_START not in events

    def test_reset(self) -> None:
        det = EnergyTurnDetector(RATE)
        for _ in range(50):
            det.accept_pcm(pcm_silence(20))
        for _ in range(50):
            det.accept_pcm(pcm_tone(20))
        assert det.in_speech
        det.reset()
        assert not det.in_speech


class TestFactory:
    def test_default_energy(self) -> None:
        assert isinstance(create_turn_detector(RATE), EnergyTurnDetector)

    def test_silero_without_model_falls_back(self) -> None:
        from core.config import ConfigManager
        ConfigManager.set("voice_turn_detector", "silero")
        ConfigManager.set("voice_silero_model_path", "/nonexistent/model.onnx")
        assert isinstance(create_turn_detector(RATE), EnergyTurnDetector)
        ConfigManager.set("voice_turn_detector", "energy")


class TestSileroTurnDetector:
    def _make(self, probs: list[float]) -> SileroTurnDetector:
        det = SileroTurnDetector.__new__(SileroTurnDetector)
        det._sample_rate = RATE
        det._session = None
        det._state = None
        det._tail = b""
        det._in_speech = False
        det._above_ms = 0.0
        det._below_ms = 0.0
        it = iter(probs)
        det._prob = lambda chunk: next(it, probs[-1])  # type: ignore[method-assign]
        return det

    def test_prob_driven_events(self) -> None:
        # 10 块高概率（300ms ≥ onset 120ms）→ START；30 块低概率（900ms ≥ silence 800ms）→ END
        det = self._make([0.9] * 10 + [0.1] * 30)
        events = [det.accept_pcm(b"\x00\x00" * 480) for _ in range(40)]
        assert TurnEvent.SPEECH_START in events
        assert TurnEvent.SPEECH_END in events
        assert not det.in_speech

    def test_hysteresis_no_flapping(self) -> None:
        # 概率在双阈值之间徘徊：不报结束（迟滞防抖动）
        det = self._make([0.9] * 10 + [0.42] * 40)
        events = [det.accept_pcm(b"\x00\x00" * 480) for _ in range(50)]
        assert TurnEvent.SPEECH_START in events
        assert TurnEvent.SPEECH_END not in events
        assert det.in_speech
