"""音频二进制帧编解码（桌面语音上行协议，前后端共用）。

```
偏移 0-3  magic        b"NEKO"（4 字节 ASCII 标识）
偏移 4-7  sample_rate  uint32 little-endian，白名单 {16000, 24000, 48000}
偏移 8..  payload      PCM16 小端原始采样（长度必须为偶数）
```

设计取舍：
- 二进制只是传输层优化，解码后进入业务层即归一化为统一语义（采样率+PCM），
  协议细节不泄漏到 voice 模块之外；
- magic + 采样率白名单 + 单帧时长上界三件套：防误路由、约束下游分支、
  以"预期帧长的倍数"做漂移门而非按字节数防 DoS。
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

AUDIO_FRAME_MAGIC = b"NEKO"  # 协议魔数（与前端客户端约定，不可改）

# 采样率白名单（Hz）：16k 覆盖 ASR 采集，24k/48k 覆盖高质量采集与播放链路
ALLOWED_SAMPLE_RATES = frozenset({16000, 24000, 48000})

# 单帧时长上限（毫秒）：采集端契约是 10-30ms 帧，120ms 留 4x 余量做漂移门
MAX_FRAME_DURATION_MS = 120

_HEADER = struct.Struct("<4sI")


class AudioFrameError(ValueError):
    """音频帧解析/校验失败（坏帧只丢帧，不关连接）。"""


@dataclass(slots=True)
class AudioFrame:
    """解码后的音频帧。"""

    sample_rate: int
    pcm: bytes
    """PCM16 小端原始采样。"""
    duration_ms: float


def decode_audio_frame(data: bytes) -> AudioFrame:
    """解析并校验一条二进制音频帧，失败抛 AudioFrameError。"""
    if len(data) < _HEADER.size + 2:
        raise AudioFrameError(f"帧长度过小: {len(data)} 字节")
    magic, sample_rate = _HEADER.unpack_from(data)
    if magic != AUDIO_FRAME_MAGIC:
        raise AudioFrameError(f"magic 不匹配: {magic!r}")
    if sample_rate not in ALLOWED_SAMPLE_RATES:
        raise AudioFrameError(f"采样率不在白名单: {sample_rate}")
    pcm = data[_HEADER.size:]
    if len(pcm) % 2 != 0:
        raise AudioFrameError("PCM16 数据长度必须为偶数")
    duration_ms = len(pcm) / 2 / sample_rate * 1000
    if duration_ms > MAX_FRAME_DURATION_MS:
        raise AudioFrameError(
            f"单帧时长 {duration_ms:.0f}ms 超过上限 {MAX_FRAME_DURATION_MS}ms"
        )
    return AudioFrame(sample_rate=sample_rate, pcm=pcm, duration_ms=duration_ms)


def encode_audio_frame(pcm: bytes, sample_rate: int) -> bytes:
    """编码一条二进制音频帧（服务端下行/测试用；入参与解码同契约）。"""
    if sample_rate not in ALLOWED_SAMPLE_RATES:
        raise AudioFrameError(f"采样率不在白名单: {sample_rate}")
    if len(pcm) % 2 != 0:
        raise AudioFrameError("PCM16 数据长度必须为偶数")
    return _HEADER.pack(AUDIO_FRAME_MAGIC, sample_rate) + pcm


def pcm16_rms(pcm: bytes) -> float:
    """PCM16 数据的均方根能量（端点检测用；array 逐样本换算，零依赖）。"""
    import array

    if not pcm:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0
    total = 0
    for s in samples:
        total += s * s
    return (total / len(samples)) ** 0.5


class EnergyVad:
    """能量法端点检测状态机（每语音会话一个实例）。

    设计（纯信号处理，零重依赖）：
    - 噪声地板 = 滑动窗口（默认 5s）RMS 的 10 分位数 + 上行限速：
      分位数对语音污染免疫（语音帧抬高 RMS 但不进低分位），
      上行限速（语音外 5%/帧、语音内 0.5%/帧）保证地板不会在语音起始
      瞬间追上信号（否则任何信号立即成为自己的地板， onset 永不触发），
      持续恒强噪声则在数秒内被地板吸收退出语音态（不锁死）；
    - 迟滞双门限：onset = floor × onset_ratio，sustain = floor × sustain_ratio
      （低于 onset），消除门限附近抖动；
    - onset 确认：连续 onset_frames 帧超 onset 门限才算语音开始（抗脉冲噪声）；
    - DC 阻断一阶滤波：抑制直流/低频轰鸣对 RMS 的干扰；
    - hangover（句尾静音容忍）由调用方按静音时长计数实现，本类只做帧级判定。
    """

    # 地板上行限速（每帧）：语音外快（适应环境变吵），语音内慢（防切断长句）
    _RISE_FAST = 1.05
    _RISE_SLOW = 1.005

    def __init__(
        self,
        *,
        floor_min: float = 100.0,
        onset_ratio: float = 3.0,
        sustain_ratio: float = 1.8,
        onset_frames: int = 2,
        window_frames: int = 250,
    ) -> None:
        from collections import deque
        self.floor_min = floor_min
        self.onset_ratio = onset_ratio
        self.sustain_ratio = sustain_ratio
        self.onset_frames = onset_frames
        self.in_speech = False
        self._onset_streak = 0
        self._dc_prev_x = 0
        self._dc_prev_y = 0.0
        self._floor = floor_min
        self._window: "deque[float]" = deque(maxlen=window_frames)

    def _filtered_rms(self, pcm: bytes) -> float:
        """DC 阻断后的 RMS（一阶 y = x - x_prev + 0.995·y_prev）。"""
        import array

        if not pcm:
            return 0.0
        samples = array.array("h")
        samples.frombytes(pcm)
        total = 0.0
        for x in samples:
            y = x - self._dc_prev_x + 0.995 * self._dc_prev_y
            self._dc_prev_x = x
            self._dc_prev_y = y
            total += y * y
        return (total / len(samples)) ** 0.5

    @property
    def floor(self) -> float:
        return self._floor

    def _update_floor(self, rms: float) -> None:
        """滑窗 10 分位数为目标，上行限速、下行即达（不低于下限）。"""
        self._window.append(rms)
        ordered = sorted(self._window)
        target = max(self.floor_min, ordered[max(0, len(ordered) // 10 - 1)])
        if target > self._floor:
            rate = self._RISE_SLOW if self.in_speech else self._RISE_FAST
            self._floor = min(target, self._floor * rate)
        else:
            self._floor = target

    def is_speech(self, pcm: bytes) -> bool:
        """帧级语音判定（内部维护地板/迟滞/onset 状态）。"""
        rms = self._filtered_rms(pcm)
        onset_threshold = self._floor * self.onset_ratio
        sustain_threshold = self._floor * self.sustain_ratio

        if self.in_speech and rms < sustain_threshold:
            # 低于维持门限：进入静音段（收束时机由调用方按时长判定）
            self.in_speech = False
            self._onset_streak = 0
        triggered = False
        if not self.in_speech and rms >= onset_threshold:
            self._onset_streak += 1
            if self._onset_streak >= self.onset_frames:
                self.in_speech = True
                self._onset_streak = 0
                triggered = True
        elif not self.in_speech:
            self._onset_streak = 0
        self._update_floor(rms)
        return triggered or self.in_speech
