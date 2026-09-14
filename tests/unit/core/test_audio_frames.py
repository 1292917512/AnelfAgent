"""音频帧编解码测试（桌宠上行协议）。"""

from __future__ import annotations

import struct

import pytest

from core.audio_frames import (
    ALLOWED_SAMPLE_RATES,
    AudioFrameError,
    decode_audio_frame,
    encode_audio_frame,
    pcm16_rms,
)


def _pcm(n_samples: int, value: int = 0) -> bytes:
    return struct.pack(f"<{n_samples}h", *([value] * n_samples))


class TestRoundTrip:
    @pytest.mark.parametrize("rate", sorted(ALLOWED_SAMPLE_RATES))
    def test_encode_decode_roundtrip(self, rate: int):
        pcm = _pcm(rate // 100, 1000)  # 10ms
        frame = decode_audio_frame(encode_audio_frame(pcm, rate))
        assert frame.sample_rate == rate
        assert frame.pcm == pcm
        assert frame.duration_ms == pytest.approx(10.0)

    def test_layout_byte_exact(self):
        """帧布局与上行协议逐字节一致（magic + LE uint32 rate + PCM16）。"""
        pcm = _pcm(480, 7)
        data = encode_audio_frame(pcm, 48000)
        assert data[:4] == b"NEKO"
        assert struct.unpack_from("<I", data, 4)[0] == 48000
        assert data[8:] == pcm


class TestValidation:
    def test_bad_magic_rejected(self):
        with pytest.raises(AudioFrameError, match="magic"):
            decode_audio_frame(b"XXXX" + struct.pack("<I", 48000) + _pcm(480))

    def test_bad_sample_rate_rejected(self):
        with pytest.raises(AudioFrameError, match="采样率"):
            decode_audio_frame(b"NEKO" + struct.pack("<I", 12345) + _pcm(100))

    def test_odd_pcm_length_rejected(self):
        with pytest.raises(AudioFrameError, match="偶数"):
            decode_audio_frame(b"NEKO" + struct.pack("<I", 48000) + b"\x01\x02\x03")

    def test_too_short_rejected(self):
        with pytest.raises(AudioFrameError, match="过小"):
            decode_audio_frame(b"NE")

    def test_duration_gate_rejects_drift(self):
        """单帧超过时长上限（默认 120ms 漂移门）拒绝。"""
        pcm = _pcm(48000)  # 1000ms
        with pytest.raises(AudioFrameError, match="时长"):
            decode_audio_frame(encode_audio_frame(pcm, 48000))

    def test_encode_rejects_bad_input(self):
        with pytest.raises(AudioFrameError):
            encode_audio_frame(_pcm(10), 12345)
        with pytest.raises(AudioFrameError):
            encode_audio_frame(b"\x01", 48000)


class TestRms:
    def test_silence_is_zero(self):
        assert pcm16_rms(_pcm(480, 0)) == 0.0

    def test_loud_signal_above_threshold(self):
        assert pcm16_rms(_pcm(480, 3000)) == pytest.approx(3000.0)

    def test_empty_is_zero(self):
        assert pcm16_rms(b"") == 0.0


class TestEnergyVad:
    """能量法端点检测：自适应地板 + 迟滞 + onset 确认。"""

    def _ac(self, value: int, n: int = 480) -> bytes:
        return struct.pack(f"<{n}h", *([value if i % 2 else -value for i in range(n)]))

    def test_silence_never_triggers(self):
        from core.audio_frames import EnergyVad
        vad = EnergyVad()
        assert not any(vad.is_speech(self._ac(0)) for _ in range(100))

    def test_onset_requires_streak(self):
        """连续 onset_frames 帧才触发（抗瞬时脉冲）。"""
        from core.audio_frames import EnergyVad
        vad = EnergyVad()
        for _ in range(50):
            vad.is_speech(self._ac(0))
        assert not vad.is_speech(self._ac(8000))  # 单帧不触发
        assert vad.is_speech(self._ac(8000))      # 连续第二帧触发
        assert vad.is_speech(self._ac(8000))

    def test_sustained_noise_absorbed_by_floor(self):
        """持续恒强噪声被滑窗地板吸收，不锁死语音态（语音内限速 0.5%/帧，
        约 5-6s 完成吸收——这是防切断长句的刻意代价）。"""
        from core.audio_frames import EnergyVad
        vad = EnergyVad()
        for _ in range(700):
            vad.is_speech(self._ac(2000))
        assert not any(vad.is_speech(self._ac(2000)) for _ in range(50))

    def test_hysteresis_sustain_below_onset(self):
        """语音态维持门限低于触发门限（迟滞）。"""
        from core.audio_frames import EnergyVad
        vad = EnergyVad()
        for _ in range(50):
            vad.is_speech(self._ac(0))
        vad.is_speech(self._ac(8000))
        vad.is_speech(self._ac(8000))
        # 信号降到 sustain 与 onset 之间：仍算语音（迟滞保持）
        assert vad.is_speech(self._ac(400))
        assert vad.in_speech
        # 低于 sustain：退出语音态
        assert not vad.is_speech(self._ac(0))
        assert not vad.in_speech

    def test_dc_signal_filtered(self):
        """恒定直流信号被 DC 阻断滤除，不触发。"""
        from core.audio_frames import EnergyVad
        vad = EnergyVad()
        dc = struct.pack("<480h", *([8000] * 480))
        for _ in range(20):
            vad.is_speech(dc)  # 字头瞬态可能触发
        assert not vad.is_speech(dc)
