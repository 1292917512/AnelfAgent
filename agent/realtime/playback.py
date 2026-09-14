"""播放链 — 重采样与分帧、有序播放队列、打断清空。

两个部件：
- PcmResampler：PCM16 流式重采样（任意源率 → 播放率，默认 24k→48k）。
  跨块边界连续：块尾未取样的输入尾部与分数相位留在实例里，下一块
  接着上块的最后位置继续——整流听感等同一次性重采样，无块间咔哒声。
  线性插值（语音带宽下质量足够，零依赖纯 numpy 向量实现）。
- PlaybackQueue：单会话有序播放队列。TTS 块经重采样后按合成块的天然
  边界入队（帧时长随块，不设上限——编码与播放端均无时长约束），
  WS 写任务逐帧取出下发；打断（barge-in）时 clear() 清空待发帧
  并放置打断哨兵——读侧立即跳到下一轮的音频，听感"立刻闭嘴"。

帧对协议：audio_chunk（数据帧，可丢弃类——队列满时丢最旧保最新）与
audio_done（收束帧，必达——标记一轮音频自然结束或被打断）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import numpy as np


class PcmResampler:
    """PCM16 流式重采样器（跨块边界连续）。"""

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        self._src_rate = src_rate
        self._dst_rate = dst_rate
        # 分数相位：下一块输入样本 0 在输出时间轴上的位置（0..1）
        self._phase = 0.0
        # 上一块的最后一个样本（边界插值的左端点）
        self._last_sample: Optional[int] = None

    @property
    def passthrough(self) -> bool:
        return self._src_rate == self._dst_rate

    def feed(self, pcm: bytes) -> bytes:
        """喂入一块源率 PCM16，返回播放率 PCM16（可能为空字节）。"""
        if self.passthrough or not pcm:
            return pcm
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        if self._last_sample is not None:
            samples = np.concatenate(([self._last_sample], samples))
        if len(samples) < 2:
            if len(samples) == 1:
                self._last_sample = int(samples[-1])
            return b""
        step = self._src_rate / self._dst_rate
        n_out = int((len(samples) - 1 - self._phase) / step)
        if n_out <= 0:
            self._phase -= len(samples) - 1
            self._last_sample = int(samples[-1])
            return b""
        positions = self._phase + step * np.arange(n_out)
        idx = positions.astype(np.int64)
        frac = positions - idx
        left = samples[idx]
        right = samples[idx + 1]
        out = left * (1 - frac) + right * frac
        self._phase = float(positions[-1] + step - (len(samples) - 1))
        self._last_sample = int(samples[-1])
        return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


@dataclass(slots=True)
class PlaybackFrame:
    """一帧待播放音频（audio_chunk/audio_done 帧对的载荷）。"""

    pcm: bytes
    sample_rate: int
    turn_id: int = 0
    final: bool = False
    """audio_done 语义：该轮音频的自然收束帧（打断不走这里，走 clear）。"""


class PlaybackQueue:
    """单会话有序播放队列（有界，满时丢最旧音频帧保最新）。"""

    _MAX_FRAMES = 100  # 100 × 120ms ≈ 12s 缓冲上限

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Optional[PlaybackFrame]] = asyncio.Queue(
            maxsize=self._MAX_FRAMES)
        self._generation = 0

    def push(self, frame: PlaybackFrame) -> None:
        """入队一帧；满时丢最旧音频帧（final 帧永远不被挤掉）。"""
        while True:
            try:
                self._queue.put_nowait(frame)
                return
            except asyncio.QueueFull:
                try:
                    self._queue.get_nowait()  # 丢最旧（音频延迟比丢帧更伤体验）
                except asyncio.QueueEmpty:
                    pass

    def finish(self, turn_id: int) -> None:
        """放置该轮的自然收束帧（audio_done）。"""
        self.push(PlaybackFrame(pcm=b"", sample_rate=0, turn_id=turn_id, final=True))

    def clear(self) -> None:
        """打断清空：丢弃全部待发帧并放置打断哨兵（读侧立即收到 None）。"""
        self._generation += 1
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    async def read(self) -> Optional[PlaybackFrame]:
        """读下一帧；None = 打断哨兵（调用方发 interrupted 收束）。"""
        return await self._queue.get()

    @property
    def generation(self) -> int:
        return self._generation
