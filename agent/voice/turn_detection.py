"""端点检测（Turn Detection）— 判"用户说完了没"的可插拔协议与实现。

协议（TurnDetector）：逐帧喂 PCM16，产出语音事件；实时引擎据此驱动
状态机（LISTENING ↔ SPEAKING 的收束点与 barge-in 的起点）。

实现梯队（voice_turn_detector 配置，缺失自动降级）：
- silero：模型级 VAD（onnxruntime + silero 模型文件），按块产出语音
  概率，迟滞双阈值 + 挂断时长判段——噪音/喘息/背景声场景显著稳于
  能量法。模型文件路径 voice_silero_model_path（放 data/models/），
  onnxruntime 或模型缺失时自动降级 energy；
- energy：能量法兜底（core.audio_frames.EnergyVad：滑窗分位数自适应
  地板 + 迟滞 + onset 确认 + DC 阻断），零依赖恒可用。
"""

from __future__ import annotations

import enum
import os
from typing import Protocol, runtime_checkable

import numpy as np

from core.audio_frames import EnergyVad
from core.config import get_config, get_config_float, get_config_int
from core.log import log

_LOG_TAG = "语音"


class TurnEvent(enum.Enum):
    """端点事件。"""

    NONE = "none"
    SPEECH_START = "speech_start"
    SPEECH_END = "speech_end"


@runtime_checkable
class TurnDetector(Protocol):
    """端点检测器协议（一次连续监听一个实例）。"""

    def accept_pcm(self, pcm: bytes) -> TurnEvent:
        """喂入一帧 PCM16，返回本帧触发的端点事件（多数帧为 NONE）。"""
        ...

    @property
    def in_speech(self) -> bool:
        """当前是否处于语音段内。"""
        ...

    def reset(self) -> None:
        """重置状态（新监听轮开始）。"""
        ...


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

    def accept_pcm(self, pcm: bytes) -> TurnEvent:
        frame_ms = len(pcm) / 2 / self._sample_rate * 1000
        onset_ms = get_config_int("voice_onset_ms", 120)
        silence_ms = get_config_int("voice_silence_ms", 800)
        if self._vad.is_speech(pcm):
            self._speech_ms += frame_ms
            self._silence_ms = 0.0
            if not self._in_speech and self._speech_ms >= onset_ms:
                self._in_speech = True
                return TurnEvent.SPEECH_START
            return TurnEvent.NONE
        # 静音帧
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
    """模型级端点检测（silero-vad ONNX）：语音概率 + 迟滞双阈值判段。

    依赖可选：onnxruntime 未安装或模型文件不存在时本类不可实例化
    （工厂函数自动降级 EnergyTurnDetector）。输入 16k 单声道 PCM16，
    按 30ms（480 样本）块推理；不足一块的尾帧攒入下一块。
    """

    _CHUNK = 480  # silero 16k 的 30ms 块

    def __init__(self, model_path: str, sample_rate: int = 16000) -> None:
        import onnxruntime  # noqa: F401 —— 缺失即抛 ImportError 由工厂降级

        self._sample_rate = sample_rate
        self._session = onnxruntime.InferenceSession(
            model_path, providers=["CPUExecutionProvider"])
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._tail = b""
        self._in_speech = False
        self._above_ms = 0.0
        self._below_ms = 0.0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._tail = b""
        self._in_speech = False
        self._above_ms = 0.0
        self._below_ms = 0.0

    def _prob(self, chunk: bytes) -> float:
        pcm = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
        inputs = {
            "input": pcm.reshape(1, -1),
            "state": self._state,
            "sr": np.array(self._sample_rate, dtype=np.int64),
        }
        out, self._state = self._session.run(None, inputs)
        return float(out[0][0])

    def accept_pcm(self, pcm: bytes) -> TurnEvent:
        onset_prob = get_config_float("voice_silero_onset_prob", 0.5)
        close_prob = get_config_float("voice_silero_close_prob", 0.35)
        onset_ms = get_config_int("voice_onset_ms", 120)
        silence_ms = get_config_int("voice_silence_ms", 800)
        event = TurnEvent.NONE
        self._tail += pcm
        while len(self._tail) >= self._CHUNK * 2:
            chunk, self._tail = self._tail[: self._CHUNK * 2], self._tail[self._CHUNK * 2:]
            prob = self._prob(chunk)
            if not self._in_speech:
                self._above_ms = self._above_ms + 30.0 if prob >= onset_prob else 0.0
                if self._above_ms >= onset_ms:
                    self._in_speech = True
                    self._below_ms = 0.0
                    event = TurnEvent.SPEECH_START
            else:
                self._below_ms = self._below_ms + 30.0 if prob < close_prob else 0.0
                if self._below_ms >= silence_ms:
                    self._in_speech = False
                    self._above_ms = 0.0
                    event = TurnEvent.SPEECH_END
        return event


def create_turn_detector(sample_rate: int = 16000) -> TurnDetector:
    """按配置创建端点检测器（silero 优先，依赖缺失自动降级 energy）。"""
    kind = str(get_config("voice_turn_detector", "energy") or "energy").strip().lower()
    if kind == "silero":
        model_path = str(get_config("voice_silero_model_path", "") or "").strip()
        if model_path and os.path.isfile(model_path):
            try:
                return SileroTurnDetector(model_path, sample_rate)
            except Exception as exc:
                log(f"silero 端点检测初始化失败（降级能量法）: {exc}",
                    "WARNING", tag=_LOG_TAG)
        else:
            log("voice_silero_model_path 未配置或模型不存在（降级能量法）",
                "DEBUG", tag=_LOG_TAG)
    return EnergyTurnDetector(sample_rate)
