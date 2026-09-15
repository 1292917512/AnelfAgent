"""端点检测（Turn Detection）— 判"用户说完了没"的可插拔协议与实现。

协议（TurnDetector）：逐帧喂 PCM16，产出语音事件；实时引擎据此驱动
状态机（LISTENING ↔ SPEAKING 的收束点与 barge-in 的起点）。

实现梯队（voice_turn_detector 配置，默认 auto 缺失自动降级）：
- smart_turn：语义端点检测——VAD 判静音后不立即收束，用本地语义模型
  评估"用户是否语义上说完了"再决定收束或继续等（停顿思考、喘息不
  误切；等待有硬上限兜底）。模型经本地模型资产下载（smart_turn）；
- silero：模型级 VAD（onnxruntime + silero v5/v6 模型），按 512 样本
  窗产出语音概率，迟滞双阈值 + 挂断时长判段。模型经本地模型资产
  下载（silero_vad）；
- energy：能量法兜底（core.audio_frames.EnergyVad：滑窗分位数自适应
  地板 + 迟滞 + onset 确认 + DC 阻断），零依赖恒可用。

模型文件在 workspace/models/（AI 工作路径），onnxruntime 未安装或
模型未下载时自动沿梯队降级，不阻断语音会话。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np

from core.audio_frames import EnergyVad
from core.config import get_config, get_config_float, get_config_int
from core.log import log

_LOG_TAG = "语音"


class TurnEvent:
    """端点事件（字符串常量：多数帧为 none）。"""

    NONE = "none"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@runtime_checkable
class TurnDetector(Protocol):
    """端点检测器协议（一次连续监听一个实例）。"""

    def accept_pcm(self, pcm: bytes) -> str:
        """喂入一帧 PCM16，返回本帧触发的端点事件。"""
        ...

    @property
    def in_speech(self) -> bool:
        """当前是否处于语音段内。"""
        ...

    def reset(self) -> None:
        """重置状态（新监听轮开始）。"""
        ...


def _frame_ms(pcm: bytes, sample_rate: int) -> float:
    return len(pcm) / 2 / sample_rate * 1000


class EnergyTurnDetector:
    """能量法端点检测：EnergyVad 帧判定 + onset/silence 时长收口。"""

    def __init__(self, sample_rate: int = 16000) -> None:
        self._vad = EnergyVad(floor_min=get_config_float("voice_vad_floor_min", 100.0))
        self._sample_rate = sample_rate
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._in_speech = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._vad = EnergyVad(floor_min=get_config_float("voice_vad_floor_min", 100.0))
        self._speech_ms = 0.0
        self._silence_ms = 0.0
        self._in_speech = False

    def accept_pcm(self, pcm: bytes) -> str:
        frame_ms = _frame_ms(pcm, self._sample_rate)
        onset_ms = get_config_int("voice_onset_ms", 120)
        silence_ms = get_config_int("voice_silence_ms", 800)
        if self._vad.is_speech(pcm):
            self._speech_ms += frame_ms
            self._silence_ms = 0.0
            if not self._in_speech and self._speech_ms >= onset_ms:
                self._in_speech = True
                return TurnEvent.SPEECH_START
            return TurnEvent.NONE
        if self._in_speech:
            self._silence_ms += frame_ms
            if self._silence_ms >= silence_ms:
                self._in_speech = False
                self._speech_ms = 0.0
                self._silence_ms = 0.0
                return TurnEvent.SPEECH_END
        else:
            self._speech_ms = max(0.0, self._speech_ms - frame_ms)
        return TurnEvent.NONE


class SileroTurnDetector:
    """模型级 VAD 端点检测（silero-vad v5/v6 ONNX）。

    每窗 512 样本（16k 下 32ms），模型输入为 [context(64) + 窗(512)]，
    LSTM 状态跨窗传递；迟滞双阈值 + 挂断时长判段。模型经本地模型
    资产（silero_vad）解析，onnxruntime/模型缺失时工厂自动降级。
    """

    WINDOW = 512
    CONTEXT = 64
    SAMPLE_RATE = 16000

    def __init__(self, model_path: str) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            model_path, sess_options=options, providers=["CPUExecutionProvider"])
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._pending = np.empty(0, dtype=np.float32)
        self._in_speech = False
        self._above_ms = 0.0
        self._below_ms = 0.0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(self.CONTEXT, dtype=np.float32)
        self._pending = np.empty(0, dtype=np.float32)
        self._in_speech = False
        self._above_ms = 0.0
        self._below_ms = 0.0

    def _probabilities(self, pcm: bytes, sample_rate: int) -> list[float]:
        if sample_rate != self.SAMPLE_RATE:
            return []  # v6 模型仅 16k；非 16k 由工厂降级能量法，此处防御
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self._pending = np.concatenate((self._pending, samples))
        probs: list[float] = []
        while self._pending.size >= self.WINDOW:
            window = self._pending[: self.WINDOW]
            self._pending = self._pending[self.WINDOW:]
            inputs = {
                "input": np.concatenate((self._context, window))[None],
                "state": self._state,
                "sr": np.asarray(self.SAMPLE_RATE, dtype=np.int64),
            }
            output, self._state = self._session.run(None, inputs)
            probs.append(float(np.asarray(output).reshape(-1)[0]))
            self._context = window[-self.CONTEXT:].copy()
        return probs

    def accept_pcm(self, pcm: bytes, sample_rate: int = 16000) -> str:
        onset_prob = get_config_float("voice_silero_onset_prob", 0.5)
        close_prob = get_config_float("voice_silero_close_prob", 0.35)
        onset_ms = get_config_int("voice_onset_ms", 120)
        silence_ms = get_config_int("voice_silence_ms", 800)
        window_ms = 1000 * self.WINDOW / self.SAMPLE_RATE
        event = TurnEvent.NONE
        for prob in self._probabilities(pcm, sample_rate):
            if not self._in_speech:
                self._above_ms = self._above_ms + window_ms if prob >= onset_prob else 0.0
                if self._above_ms >= onset_ms:
                    self._in_speech = True
                    self._below_ms = 0.0
                    event = TurnEvent.SPEECH_START
            else:
                self._below_ms = self._below_ms + window_ms if prob < close_prob else 0.0
                if self._below_ms >= silence_ms:
                    self._in_speech = False
                    self._above_ms = 0.0
                    event = TurnEvent.SPEECH_END
        return event


# ======================================================================
# 语义端点检测（SmartTurn）
# ======================================================================

def _mel_filter_bank() -> np.ndarray:
    """Whisper 的 Slaney 归一化 80 通道 mel 滤波组（201 个 FFT 频点）。"""
    def hz_to_mel(freq: np.ndarray) -> np.ndarray:
        min_log_hz, min_log_mel = 1000.0, 15.0
        logstep = np.log(6.4) / 27.0
        mel = freq / (200.0 / 3.0)
        return np.where(
            freq >= min_log_hz,
            min_log_mel + np.log(np.maximum(freq, min_log_hz) / min_log_hz) / logstep,
            mel)

    def mel_to_hz(mel: np.ndarray) -> np.ndarray:
        min_log_hz, min_log_mel = 1000.0, 15.0
        logstep = np.log(6.4) / 27.0
        return np.where(
            mel >= min_log_mel,
            min_log_hz * np.exp(logstep * (mel - min_log_mel)),
            (200.0 / 3.0) * mel)

    fft_freqs = np.linspace(0.0, 8000.0, 201)
    mel_min, mel_max = hz_to_mel(np.asarray([0.0, 8000.0]))
    filters_freq = mel_to_hz(np.linspace(mel_min, mel_max, 82))
    diff = np.diff(filters_freq)
    slopes = filters_freq[None, :] - fft_freqs[:, None]
    down = -slopes[:, :-2] / diff[:-1]
    up = slopes[:, 2:] / diff[1:]
    filters = np.maximum(0.0, np.minimum(down, up))
    filters *= (2.0 / (filters_freq[2:82] - filters_freq[:80]))[None, :]
    return filters


class SmartTurnRuntime:
    """语义端点模型运行时：Whisper log-mel 特征 + CPU ONNX 推理。

    输入为尾部 8 秒 16k 单声道，输出"该轮已说完"的概率。进程内共享
    单实例（会话间无状态）；连续推理失败即熔断（降级回 VAD 行为）。
    """

    SAMPLE_RATE = 16000
    MAX_SAMPLES = 8 * SAMPLE_RATE
    N_FFT = 400
    HOP = 160
    N_FRAMES = 800

    def __init__(self, model_path: str) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self._session = ort.InferenceSession(
            model_path, sess_options=options, providers=["CPUExecutionProvider"])
        inputs = self._session.get_inputs()
        if len(inputs) != 1:
            raise ValueError("语义端点模型输入契约不符（应仅有 input_features）")
        self._input_name = inputs[0].name
        self._filters = _mel_filter_bank()
        self._window = 0.5 - 0.5 * np.cos(
            2 * np.pi * np.arange(self.N_FFT, dtype=np.float64) / self.N_FFT)
        self._frame_indices = (
            np.arange(self.N_FFT)[None, :]
            + self.HOP * np.arange(self.N_FRAMES + 1)[:, None])

    def _features(self, audio: np.ndarray) -> np.ndarray:
        """尾部 8 秒 → [1, 80, 800] log-mel 特征。"""
        values = np.asarray(audio, dtype=np.float64)
        if values.size > self.MAX_SAMPLES:
            values = values[-self.MAX_SAMPLES:]
        elif values.size < self.MAX_SAMPLES:
            values = np.pad(values, (self.MAX_SAMPLES - values.size, 0))
        values = (values - values.mean()) / np.sqrt(values.var() + 1e-7)
        centered = np.pad(values, (self.N_FFT // 2, self.N_FFT // 2), mode="reflect")
        frames = centered[self._frame_indices] * self._window[None, :]
        spectrum = np.fft.rfft(frames, n=self.N_FFT, axis=1)
        power = (spectrum.real ** 2 + spectrum.imag ** 2).T
        mel = self._filters.T @ power
        log_spectrum = np.log10(np.clip(mel, 1e-10, None))[:, :-1]
        log_spectrum = np.maximum(log_spectrum, log_spectrum.max() - 8.0)
        return ((log_spectrum + 4.0) / 4.0).astype(np.float32)[None]

    def predict(self, audio: np.ndarray) -> float:
        """尾部 8 秒音频 → 说完概率 [0,1]。"""
        outputs = self._session.run(None, {self._input_name: self._features(audio)})
        prob = float(np.asarray(outputs[0]).reshape(-1)[0])
        if not 0.0 <= prob <= 1.0:
            raise ValueError(f"语义端点模型输出越界: {prob}")
        return prob


_runtime: Optional[SmartTurnRuntime] = None
_runtime_failed = False


def _get_smart_turn() -> Optional[SmartTurnRuntime]:
    """语义端点运行时单例（模型未就绪/加载失败返回 None）。"""
    global _runtime, _runtime_failed
    if _runtime_failed or _runtime is not None:
        return _runtime
    from agent.model_assets import get_model_asset_manager

    path = get_model_asset_manager().resolve("smart_turn")
    if path is None:
        return None
    try:
        _runtime = SmartTurnRuntime(path)
        log("语义端点模型已加载（smart_turn）", "DEBUG", tag=_LOG_TAG)
    except Exception as exc:
        _runtime_failed = True
        log(f"语义端点模型加载失败（本次运行不再尝试）: {exc}", "WARNING", tag=_LOG_TAG)
        return None
    return _runtime


def reset_smart_turn_runtime() -> None:
    """清空运行时缓存（模型更新/测试后重建）。"""
    global _runtime, _runtime_failed
    _runtime = None
    _runtime_failed = False


class SmartTurnTurnDetector:
    """语义端点检测：VAD 判静音收束点后，语义模型确认再收束。

    融合策略——VAD 提议、语义裁决：
    - 基座检测器（silero/energy）触发 SPEECH_END 时进入候选等待，
      对尾部 8 秒音频推理"说完概率"；
    - 概率 ≥ 阈值 → 收束；否则继续收听，静音期间按评估间隔复评，
      用户重新开口（基座再触发 START）则取消候选继续同一段；
    - 静音累计到硬上限仍未确认 → 强制收束（绝不挂死）；
    - 模型不可用/连续推理失败 → 退化为基座行为。
    """

    def __init__(self, base: TurnDetector, sample_rate: int = 16000) -> None:
        self._base = base
        self._sample_rate = sample_rate
        self._audio = np.empty(0, dtype=np.float32)
        self._candidate = False
        self._silence_ms = 0.0
        self._last_eval_ms = 0.0
        self._in_speech = False
        self._consecutive_errors = 0
        self._degraded = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._base.reset()
        self._candidate = False
        self._silence_ms = 0.0
        self._last_eval_ms = 0.0
        self._in_speech = False

    def _append(self, pcm: bytes) -> None:
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self._audio = np.concatenate((self._audio, samples))[-SmartTurnRuntime.MAX_SAMPLES:]

    def _evaluate(self) -> Optional[bool]:
        """语义裁决：True=收束 / False=继续等 / None=模型不可用。"""
        runtime = _get_smart_turn()
        if runtime is None or self._audio.size == 0:
            return None
        try:
            prob = runtime.predict(self._audio)
            self._consecutive_errors = 0
            return prob >= get_config_float("voice_smart_turn_threshold", 0.5)
        except Exception as exc:
            self._consecutive_errors += 1
            log(f"语义端点推理失败({self._consecutive_errors}): {exc}",
                "DEBUG", tag=_LOG_TAG)
            if self._consecutive_errors >= 3:
                self._degraded = True
                log("语义端点连续失败，本次会话降级为纯 VAD", "WARNING", tag=_LOG_TAG)
            return None

    def accept_pcm(self, pcm: bytes) -> str:
        self._append(pcm)
        event = self._base.accept_pcm(pcm)
        if self._candidate:
            # 候选等待期：用户重新开口 → 取消候选，继续同一段语音
            if event == TurnEvent.SPEECH_START:
                self._candidate = False
                self._silence_ms = 0.0
                return TurnEvent.NONE
            if event == TurnEvent.SPEECH_END:
                event = TurnEvent.NONE  # 基座重复触发，以语义裁决为准
            self._silence_ms += _frame_ms(pcm, self._sample_rate)
            if self._degraded or self._silence_ms >= get_config_int(
                    "voice_smart_turn_max_silence_ms", 3000):
                return self._end()
            interval = get_config_int("voice_smart_turn_eval_interval_ms", 250)
            if self._silence_ms - self._last_eval_ms >= interval:
                self._last_eval_ms = self._silence_ms
                if self._evaluate() is True:
                    return self._end()
            return TurnEvent.NONE
        if event == TurnEvent.SPEECH_START:
            self._in_speech = True
        elif event == TurnEvent.SPEECH_END:
            if self._degraded or _get_smart_turn() is None:
                self._in_speech = False
                return TurnEvent.SPEECH_END  # 模型不可用：退化为基座行为
            self._in_speech = True  # 候选期仍算语音段内
            self._candidate = True
            self._silence_ms = 0.0
            self._last_eval_ms = 0.0
            if self._evaluate() is True:
                return self._end()
            return TurnEvent.NONE
        return event

    def _end(self) -> str:
        self._candidate = False
        self._in_speech = False
        self._silence_ms = 0.0
        return TurnEvent.SPEECH_END


def _silero_detector(sample_rate: int) -> Optional[SileroTurnDetector]:
    if sample_rate != SileroTurnDetector.SAMPLE_RATE:
        return None
    from agent.model_assets import get_model_asset_manager

    path = get_model_asset_manager().resolve("silero_vad")
    if path is None:
        return None
    try:
        return SileroTurnDetector(path)
    except Exception as exc:
        log(f"silero VAD 初始化失败: {exc}", "WARNING", tag=_LOG_TAG)
        return None


def _smart_turn_detector(sample_rate: int) -> Optional[SmartTurnTurnDetector]:
    if sample_rate != SmartTurnRuntime.SAMPLE_RATE:
        return None
    if _get_smart_turn() is None:
        return None
    base = _silero_detector(sample_rate) or EnergyTurnDetector(sample_rate)
    return SmartTurnTurnDetector(base, sample_rate)


def create_turn_detector(sample_rate: int = 16000) -> TurnDetector:
    """按配置创建端点检测器（voice_turn_detector，缺失自动沿梯队降级）。"""
    kind = str(get_config("voice_turn_detector", "auto") or "auto").strip().lower()
    detector: Optional[TurnDetector] = None
    if kind in ("auto", "smart_turn"):
        detector = _smart_turn_detector(sample_rate)
        if detector is not None:
            log("端点检测: 语义端点(smart_turn)", "DEBUG", tag=_LOG_TAG)
            return detector
        if kind == "smart_turn":
            log("smart_turn 模型未就绪，沿梯队降级", "WARNING", tag=_LOG_TAG)
    if kind in ("auto", "silero"):
        detector = _silero_detector(sample_rate)
        if detector is not None:
            log("端点检测: silero VAD", "DEBUG", tag=_LOG_TAG)
            return detector
        if kind == "silero":
            log("silero 模型未就绪，降级能量法", "WARNING", tag=_LOG_TAG)
    log("端点检测: 能量法", "DEBUG", tag=_LOG_TAG)
    return EnergyTurnDetector(sample_rate)


def detector_status() -> dict:
    """端点检测当前生效档位（配置 × 模型在位情况，与工厂同规则的静态解析）。

    供声音页与 realtime_status 展示"实际在用哪一档"；只查文件与运行时
    在位性（哈希缓存），不加载模型。
    """
    from agent.model_assets import get_model_asset_manager, runtime_ready

    kind = str(get_config("voice_turn_detector", "auto") or "auto").strip().lower()
    mgr = get_model_asset_manager()
    ort_ready = runtime_ready(mgr.asset("silero_vad"))
    silero_ready = ort_ready and mgr.resolve("silero_vad") is not None
    smart_ready = ort_ready and mgr.resolve("smart_turn") is not None and not _runtime_failed

    effective, base = "energy", "energy"
    if kind in ("auto", "smart_turn") and smart_ready:
        effective = "smart_turn"
        base = "silero" if silero_ready else "energy"
    elif kind in ("auto", "silero") and silero_ready:
        effective = "silero"
    return {
        "configured": kind,
        "effective": effective,
        "base": base,
        "runtime_ready": ort_ready,
        "models": {
            "silero_vad": "ready" if silero_ready else "missing",
            "smart_turn": "ready" if smart_ready else "missing",
        },
    }
