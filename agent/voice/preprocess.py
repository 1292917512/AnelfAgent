"""麦克风输入预处理链 — 谱减降噪 → 自动增益 → 限幅。

逐帧流式处理（STFT 重叠相加），任意采样率可用（帧长取 32ms），
纯 numpy 实现零外部依赖；两级均可在配置中独立开关（voice_denoise /
voice_agc），全关时字节直通。

链路职责：
- 降噪（谱减）：静音帧持续学习稳态噪声谱，语音帧按超减法抑制；
  噪声谱未建立前直通（不碰可能的开头语音）；
- 自动增益（AGC）：语音帧向目标响度自适应放大（只增不减、上限限幅），
  静音帧保持增益（不放大底噪）；
- 限幅：峰值软顶防削波，快速压制慢恢复。

处理引入一个跳距（约 16ms）的成帧时延，对端点检测与 ASR 无感。
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from core.config import get_config_bool

# 谱减参数
_OVER_SUBTRACT = 2.0  # 超减系数：宁可多减，残留少
_GAIN_FLOOR_DB = -12.0  # 单频点最小增益（过度压制会产生音乐噪声）
_NOISE_LEARN_ALPHA = 0.2  # 噪声谱指数滑动系数
_NOISE_WARMUP_FRAMES = 3  # 建立噪声谱所需的连续静音帧数
_NOISE_MAX_RMS = 0.05  # 初始学习绝对门：仅接受该响度以下的帧为噪声候选
_QUIET_FACTOR = 1.5  # 帧响度低于噪声地板该倍数视为静音（学习噪声）

# AGC / 限幅参数
_AGC_TARGET_RMS = 0.08
_AGC_GATE_RMS = 0.01  # 降噪关闭时的静音判据（低于不参与增益自适应）
_AGC_SILENCE_RMS = 1e-3  # 近数字静音（≈-60dBFS）：任何情况下都保持增益
_AGC_MAX_GAIN = 8.0  # 最大增益（+18dB）
_AGC_ATTACK_TAU = 0.05  # 增益上升时间常数（秒，放大要慢）
_AGC_RELEASE_TAU = 0.2  # 增益下降时间常数（秒，收紧要快一些）
_LIMIT_CEILING = 0.98
_LIMIT_RELEASE_TAU = 0.1


class _StftStream:
    """流式 STFT 重叠相加：帧定界、逐帧回调、按跳距吐出定帧样本。

    窗为 sqrt-Hann、50% 重叠（合成满足恒定和）；每处理完一帧，其
    首 hop 个样本的全部贡献已齐备，即可定帧输出。
    """

    def __init__(self, sample_rate: int) -> None:
        self.frame_size = int(round(sample_rate * 0.032 / 2)) * 2  # 32ms，偶数
        self.hop = self.frame_size // 2
        self._window = np.sqrt(
            0.5 - 0.5 * np.cos(2 * np.pi * np.arange(self.frame_size) / self.frame_size))
        self._buf = np.empty(0, dtype=np.float32)
        self._acc = np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        self._buf = np.empty(0, dtype=np.float32)
        self._acc = np.zeros(0, dtype=np.float32)

    def flush(self, process_frame: Callable[[np.ndarray], np.ndarray]) -> np.ndarray:
        """收尾冲刷：补零完成挂起的半帧，吐尽全部定帧样本（流结束前调用）。"""
        if self._buf.size == 0:
            return np.empty(0, dtype=np.float32)
        pad = self.frame_size - self._buf.size
        self._buf = np.concatenate((self._buf, np.zeros(pad, dtype=np.float32)))
        self._acc = np.concatenate((self._acc, np.zeros(pad, dtype=np.float32)))
        windowed = self._buf * self._window
        processed = np.asarray(process_frame(windowed))
        if np.iscomplexobj(processed):
            processed = np.fft.irfft(processed, n=self.frame_size)
        self._acc[: self.frame_size] += processed.astype(np.float32) * self._window
        out = self._acc.copy()
        self._buf = np.empty(0, dtype=np.float32)
        self._acc = np.zeros(0, dtype=np.float32)
        return out

    def feed(
        self, samples: np.ndarray, process_frame: Callable[[np.ndarray], np.ndarray],
    ) -> np.ndarray:
        """喂入样本（process_frame(加窗帧)→处理后的频域/时域帧），返回定帧输出。"""
        self._buf = np.concatenate((self._buf, samples.astype(np.float32)))
        self._acc = np.concatenate(
            (self._acc, np.zeros(len(samples), dtype=np.float32)))
        out = np.empty(0, dtype=np.float32)
        while self._buf.size >= self.frame_size:
            windowed = self._buf[: self.frame_size] * self._window
            processed = np.asarray(process_frame(windowed))
            if np.iscomplexobj(processed):
                processed = np.fft.irfft(processed, n=self.frame_size)
            synth = processed.astype(np.float32) * self._window
            self._acc[: self.frame_size] += synth
            out = np.concatenate((out, self._acc[: self.hop].copy()))
            self._buf = self._buf[self.hop:]
            self._acc = self._acc[self.hop:]
        return out


class SpectralDenoiser:
    """稳态噪声谱减：静音帧学噪声谱，语音帧超减抑制。"""

    def __init__(self, frame_size: int) -> None:
        self._frame_size = frame_size
        self._noise_power: np.ndarray | None = None
        self._noise_rms = 0.0
        self._learned = 0

    def reset(self) -> None:
        self._noise_power = None
        self._noise_rms = 0.0
        self._learned = 0

    def ready(self) -> bool:
        """噪声谱是否已建立（可执行抑制）。"""
        return self._noise_power is not None and self._learned >= _NOISE_WARMUP_FRAMES

    def process(self, windowed: np.ndarray) -> np.ndarray:
        """加窗帧 → 频域帧（噪声谱未建立时原样返回时域帧）。"""
        rms = float(np.sqrt(np.mean(windowed ** 2) + 1e-12))
        spectrum = np.fft.rfft(windowed)
        power = np.abs(spectrum) ** 2
        if self.ready():
            learn = rms < self._noise_rms * _QUIET_FACTOR
        else:
            # 建立期：绝对电平门 + 与已见噪声同量级（宽界），防语音开头被学成噪声
            learn = rms < _NOISE_MAX_RMS and (
                self._noise_power is None or rms < self._noise_rms * 8.0)
        if learn:
            a = _NOISE_LEARN_ALPHA
            if self._noise_power is None:
                self._noise_power = power.copy()
                self._noise_rms = rms
            else:
                self._noise_power = (1 - a) * self._noise_power + a * power
                self._noise_rms = (1 - a) * self._noise_rms + a * rms
            self._learned += 1
        elif not self.ready():
            # 建立期被响帧打断：半成品估计大概率混入语音，弃之重来
            self._noise_power = None
            self._noise_rms = 0.0
            self._learned = 0
        noise = self._noise_power
        if noise is None or not self.ready():
            return windowed  # 噪声谱未建立，直通
        gain = 1.0 - _OVER_SUBTRACT * noise / (power + 1e-12)
        gain = np.maximum(gain, 10 ** (_GAIN_FLOOR_DB / 20))
        return spectrum * gain

    def is_quiet(self, rms: float) -> bool:
        """帧响度是否属于稳态噪声（未建立期视为静音，AGC 据此保持增益）。"""
        if not self.ready():
            return True
        return 0.0 < rms < self._noise_rms * _QUIET_FACTOR


class GainController:
    """自动增益：语音帧向目标响度自适应（只放大不衰减，静音帧保持）。"""

    def __init__(self, sample_rate: int, step_samples: int) -> None:
        self._sample_rate = sample_rate
        self._step = step_samples
        self._gain = 1.0

    def reset(self) -> None:
        self._gain = 1.0

    def process(self, frame: np.ndarray, quiet: bool) -> np.ndarray:
        rms = float(np.sqrt(np.mean(frame ** 2) + 1e-12))
        if quiet or rms < _AGC_SILENCE_RMS:
            return frame * self._gain  # 静音/底噪帧：维持当前增益
        desired = min(_AGC_TARGET_RMS / max(rms, 1e-6), _AGC_MAX_GAIN)
        tau = _AGC_ATTACK_TAU if desired > self._gain else _AGC_RELEASE_TAU
        alpha = 1.0 - np.exp(-self._step / (tau * self._sample_rate))
        self._gain += alpha * (desired - self._gain)
        return frame * self._gain


class Limiter:
    """峰值限幅：超顶即压（瞬时攻击），慢恢复。"""

    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._gain = 1.0

    def reset(self) -> None:
        self._gain = 1.0

    def process(self, samples: np.ndarray) -> np.ndarray:
        if samples.size:
            peak = float(np.max(np.abs(samples)))
            if peak * self._gain > _LIMIT_CEILING and peak > 0:
                self._gain = _LIMIT_CEILING / peak
            else:
                alpha = 1.0 - np.exp(
                    -samples.size / (_LIMIT_RELEASE_TAU * self._sample_rate))
                self._gain += alpha * (1.0 - self._gain)
        return samples * self._gain


class PcmPreprocessor:
    """PCM16 帧级预处理入口：降噪 → AGC → 限幅（全关字节直通）。

    输出可能为空（内部缓冲未凑满一帧）；调用方按返回字节数计时。
    """

    def __init__(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate
        self._denoise_on = get_config_bool("voice_denoise", True)
        self._agc_on = get_config_bool("voice_agc", True)
        self._limiter = Limiter(sample_rate)
        self._stft = _StftStream(sample_rate)
        self._denoise = SpectralDenoiser(self._stft.frame_size)
        self._gain = GainController(sample_rate, self._stft.hop)
        self._passthrough = not (self._denoise_on or self._agc_on)

    def reset(self) -> None:
        self._denoise.reset()
        self._gain.reset()
        self._limiter.reset()
        self._stft.reset()

    def feed(self, pcm: bytes) -> bytes:
        if self._passthrough or len(pcm) < 2:
            return pcm
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        out = self._limiter.process(self._stft.feed(samples, self._process_frame))
        return (np.clip(out, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    def flush(self) -> bytes:
        """收尾冲刷（会话成段/轮次收束前调用，吐尽内部滞留样本）。"""
        if self._passthrough:
            return b""
        out = self._limiter.process(self._stft.flush(self._process_frame))
        return (np.clip(out, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()

    def _process_frame(self, windowed: np.ndarray) -> np.ndarray:
        raw_rms = float(np.sqrt(np.mean(windowed ** 2) + 1e-12))
        processed = self._denoise.process(windowed) if self._denoise_on else windowed
        if np.iscomplexobj(processed):
            processed = np.fft.irfft(processed, n=len(windowed))
        time_frame = processed.astype(np.float32)
        if self._agc_on:
            # 静音判据取降噪器的噪声地板（未建立期视为静音，不放大底噪）
            quiet = (
                self._denoise.is_quiet(raw_rms) if self._denoise_on
                else raw_rms < _AGC_GATE_RMS
            )
            time_frame = self._gain.process(time_frame, quiet)
        return time_frame


def create_preprocessor(sample_rate: int) -> PcmPreprocessor:
    """按配置构建预处理链（voice_denoise / voice_agc，全关直通零开销）。"""
    return PcmPreprocessor(sample_rate)
