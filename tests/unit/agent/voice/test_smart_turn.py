"""语义端点检测测试：候选等待 / 语义裁决 / 复评与硬上限 / 续说取消 / 降级。"""

from __future__ import annotations

import numpy as np

from agent.voice import turn_detection as td
from agent.voice.turn_detection import SmartTurnTurnDetector, TurnEvent

RATE = 16000
FRAME = b"\x00\x00" * 320  # 20ms


class FakeBase:
    """脚本化基座：按喂入时长阈值触发 START/END（与真实 VAD 语义一致）。"""

    def __init__(self, speech_ms: int, silence_ms: int) -> None:
        self._speech_at = speech_ms
        self._end_at = silence_ms
        self._speech = 0.0
        self._silence = 0.0
        self._in_speech = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._speech = self._silence = 0.0
        self._in_speech = False

    def accept_pcm(self, pcm: bytes) -> str:
        ms = len(pcm) / 2 / RATE * 1000
        loud = any(pcm)  # 非全零即响帧
        if loud:
            self._silence = 0.0
            if not self._in_speech:
                self._speech += ms
                if self._speech >= self._speech_at:
                    self._in_speech = True
                    return TurnEvent.SPEECH_START
            return TurnEvent.NONE
        if self._in_speech:
            self._silence += ms
            if self._silence >= self._end_at:
                self._in_speech = False
                return TurnEvent.SPEECH_END
        return TurnEvent.NONE


class FakeRuntime:
    """语义模型桩：按调用序吐概率，耗尽后回落末值。"""

    def __init__(self, probs: list[float]) -> None:
        self.calls = 0
        self._probs = list(probs)
        self._last = probs[-1] if probs else 0.0

    def predict(self, audio) -> float:
        self.calls += 1
        if self._probs:
            self._last = self._probs.pop(0)
        return self._last


def _armed(det: SmartTurnTurnDetector, base: FakeBase) -> SmartTurnTurnDetector:
    """喂响帧直到语音开始（12 × 20ms = 240ms ≥ onset 120ms）。"""
    for _ in range(12):
        det.accept_pcm(FRAME.replace(b"\x00", b"\x01", 1))
    assert base.in_speech
    return det


class TestSemanticFusion:
    def test_confirm_immediately_when_probable(self, monkeypatch) -> None:
        monkeypatch.setattr(td, "_runtime", FakeRuntime([0.9]))
        base = FakeBase(speech_ms=120, silence_ms=800)
        det = _armed(SmartTurnTurnDetector(base, RATE), base)
        events = [det.accept_pcm(FRAME) for _ in range(80)]  # 基座 END 于 800ms，语义 0.9
        assert TurnEvent.SPEECH_END in events
        assert not det.in_speech

    def test_wait_then_confirm_on_reeval(self, monkeypatch) -> None:
        monkeypatch.setattr(td, "_runtime", FakeRuntime([0.1, 0.1, 0.1, 0.1, 0.9, 0.9]))
        base = FakeBase(speech_ms=120, silence_ms=800)
        det = _armed(SmartTurnTurnDetector(base, RATE), base)
        events = [det.accept_pcm(FRAME) for _ in range(80)]  # 首评 0.1 → 不收束
        assert TurnEvent.SPEECH_END not in events
        assert det.in_speech  # 候选期仍算语音段内
        events += [det.accept_pcm(FRAME) for _ in range(25)]  # 250ms 复评后 0.9
        assert TurnEvent.SPEECH_END in events

    def test_force_end_at_max_silence(self, monkeypatch) -> None:
        monkeypatch.setattr(td, "_runtime", FakeRuntime([0.0]))
        base = FakeBase(speech_ms=120, silence_ms=800)
        det = _armed(SmartTurnTurnDetector(base, RATE), base)
        events = [det.accept_pcm(FRAME) for _ in range(300)]  # 6000ms 静音
        assert TurnEvent.SPEECH_END in events  # 硬上限 3000ms 强制收束

    def test_resume_cancels_candidate(self, monkeypatch) -> None:
        monkeypatch.setattr(td, "_runtime", FakeRuntime([0.1]))
        base = FakeBase(speech_ms=120, silence_ms=800)
        det = _armed(SmartTurnTurnDetector(base, RATE), base)
        for _ in range(90):  # 过基座 END，进入候选等待
            det.accept_pcm(FRAME)
        assert det.in_speech
        # 用户重新开口：基座再次 START，语义层吞掉（仍是同一段语音）
        events = [det.accept_pcm(FRAME.replace(b"\x00", b"\x01", 1)) for _ in range(30)]
        assert TurnEvent.SPEECH_START not in events
        assert det.in_speech

    def test_runtime_missing_degrades_to_base(self, monkeypatch) -> None:
        monkeypatch.setattr(td, "_runtime", None)
        base = FakeBase(speech_ms=120, silence_ms=800)
        det = _armed(SmartTurnTurnDetector(base, RATE), base)
        events = [det.accept_pcm(FRAME) for _ in range(80)]  # 无模型
        assert TurnEvent.SPEECH_END in events  # 基座 END 直接透传
        assert not det.in_speech

    def test_consecutive_errors_degrade_and_end(self, monkeypatch) -> None:
        class _Boom:
            def predict(self, audio):
                raise RuntimeError("boom")

        monkeypatch.setattr(td, "_runtime", _Boom())
        base = FakeBase(speech_ms=120, silence_ms=800)
        det = _armed(SmartTurnTurnDetector(base, RATE), base)
        events = [det.accept_pcm(FRAME) for _ in range(90)]  # 候选期反复推理失败
        assert TurnEvent.SPEECH_START not in events
        assert det._degraded  # 连续 3 次失败熔断
        events += [det.accept_pcm(FRAME) for _ in range(10)]  # 熔断后即收束
        assert TurnEvent.SPEECH_END in events


class TestFeatureExtraction:
    def _runtime(self) -> td.SmartTurnRuntime:
        runtime = td.SmartTurnRuntime.__new__(td.SmartTurnRuntime)
        runtime._filters = td._mel_filter_bank()
        runtime._window = 0.5 - 0.5 * np.cos(
            2 * np.pi * np.arange(td.SmartTurnRuntime.N_FFT) / td.SmartTurnRuntime.N_FFT)
        runtime._frame_indices = (
            np.arange(td.SmartTurnRuntime.N_FFT)[None, :]
            + td.SmartTurnRuntime.HOP
            * np.arange(td.SmartTurnRuntime.N_FRAMES + 1)[:, None])
        return runtime

    def test_features_shape(self) -> None:
        audio = np.random.default_rng(7).normal(
            0, 0.1, td.SmartTurnRuntime.SAMPLE_RATE).astype(np.float32)
        feats = self._runtime()._features(audio)
        assert feats.shape == (1, 80, 800)
        assert feats.dtype == np.float32
        assert bool(np.isfinite(feats).all())

    def test_mel_filter_bank_shape(self) -> None:
        filters = td._mel_filter_bank()
        assert filters.shape == (201, 80)
        assert float(filters.min()) >= 0.0
