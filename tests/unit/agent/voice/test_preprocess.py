"""输入预处理链测试：直通一致性 / 降噪抑制 / 语音保形 / AGC 拉升 / 限幅。"""

from __future__ import annotations

import numpy as np
import pytest

from agent.voice.preprocess import PcmPreprocessor, create_preprocessor

RATE = 16000


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)))


def to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def run(pcm: bytes, chunk: int = 1920) -> np.ndarray:
    pre = PcmPreprocessor(RATE)
    out = b""
    for i in range(0, len(pcm), chunk):
        out += pre.feed(pcm[i:i + chunk])
    return np.frombuffer(out, dtype="<i2").astype(np.float32) / 32768.0


@pytest.fixture
def speech_and_noise():
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.02, RATE).astype(np.float32)
    speech = (0.3 * np.sin(2 * np.pi * 220 * np.arange(RATE) / RATE)
              + rng.normal(0, 0.02, RATE)).astype(np.float32)
    return noise, speech


class TestPassthrough:
    def test_all_off_returns_identical_bytes(self, monkeypatch) -> None:
        import agent.voice.preprocess as pp

        monkeypatch.setattr(pp, "get_config_bool", lambda key, default: False)
        pre = create_preprocessor(RATE)
        pcm = to_pcm(np.random.default_rng(1).normal(0, 0.1, 4800).astype(np.float32))
        assert pre.feed(pcm) == pcm

    def test_empty_input_passthrough(self) -> None:
        pre = PcmPreprocessor(RATE)
        assert pre.feed(b"") == b""


class TestDenoise:
    def test_trailing_noise_suppressed(self, speech_and_noise) -> None:
        noise, speech = speech_and_noise
        out = run(to_pcm(np.concatenate([noise, speech, noise])))
        suppression_db = 20 * np.log10(rms(out[-4800:-512]) / rms(noise[:4800]))
        assert suppression_db < -6.0  # 稳态噪声 ≥6dB 抑制

    def test_speech_energy_preserved(self, speech_and_noise) -> None:
        noise, speech = speech_and_noise
        out = run(to_pcm(np.concatenate([noise, speech, noise])))
        kept = rms(out[RATE + 512: 2 * RATE - 512]) / rms(speech)
        assert 0.3 < kept < 2.5  # 语音不被摧毁（AGC 允许向目标响度调整）

    def test_speech_head_not_learned_as_noise(self) -> None:
        # 开头即语音：噪声谱不该建立，语音应基本原样通过
        speech = (0.2 * np.sin(2 * np.pi * 180 * np.arange(RATE) / RATE)).astype(np.float32)
        out = run(to_pcm(speech))
        kept = rms(out[512:RATE - 512]) / rms(speech)
        assert kept > 0.7

    def test_output_length_conserved(self) -> None:
        pcm = to_pcm(np.random.default_rng(3).normal(0, 0.05, 2 * RATE).astype(np.float32))
        pre = PcmPreprocessor(RATE)
        out = b""
        for i in range(0, len(pcm), 1920):
            out += pre.feed(pcm[i:i + 1920])
        # 输出 = 输入 - 内部滞留（≤ 一个帧长）
        assert 0 <= len(pcm) - len(out) <= 2 * pre._stft.frame_size


class TestAgcAndLimiter:
    def test_weak_speech_lifted_not_noise(self) -> None:
        rng = np.random.default_rng(7)
        silence = rng.normal(0, 0.002, RATE // 2).astype(np.float32)
        weak = (0.012 * np.sin(2 * np.pi * 220 * np.arange(RATE) / RATE)).astype(np.float32)
        out = run(to_pcm(np.concatenate([silence, weak, silence])))
        lifted = rms(out[RATE // 2 + 512: RATE // 2 + RATE - 512]) / rms(weak)
        assert lifted > 2.0  # 弱语音显著拉升
        # 底噪不被放大到语音量级
        assert rms(out[-4800:-512]) < 0.02

    def test_peak_capped(self) -> None:
        loud = (0.95 * np.sin(2 * np.pi * 300 * np.arange(RATE) / RATE)
                + np.random.default_rng(2).normal(0, 0.01, RATE)).astype(np.float32)
        lead = np.random.default_rng(2).normal(0, 0.01, RATE // 2).astype(np.float32)
        out = run(to_pcm(np.concatenate([lead, loud])))
        assert float(np.max(np.abs(out))) <= 0.99  # 限幅天花板

    def test_reset_clears_state(self) -> None:
        pre = PcmPreprocessor(RATE)
        pre.feed(to_pcm(np.random.default_rng(5).normal(0, 0.1, 4800).astype(np.float32)))
        pre.reset()
        assert pre._gain._gain == 1.0
        assert not pre._denoise.ready()
        assert pre._stft._buf.size == 0
