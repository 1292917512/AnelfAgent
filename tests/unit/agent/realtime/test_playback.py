"""播放链测试：重采样边界连续 / 播放队列溢出与打断。"""

from __future__ import annotations

import math

from agent.realtime.playback import PcmResampler, PlaybackFrame, PlaybackQueue


def pcm_sine(rate: int, ms: int, freq: float = 440.0, amp: int = 8000) -> bytes:
    n = int(rate * ms / 1000)
    frames = bytearray()
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * freq * i / rate))
        frames += v.to_bytes(2, "little", signed=True)
    return bytes(frames)


class TestPcmResampler:
    def test_passthrough_same_rate(self) -> None:
        rs = PcmResampler(24000, 24000)
        data = pcm_sine(24000, 100)
        assert rs.feed(data) == data

    def test_doubles_sample_count(self) -> None:
        rs = PcmResampler(24000, 48000)
        out = rs.feed(pcm_sine(24000, 100))
        # 2400 输入样本 → ≈4800 输出样本（相位残差 ±2）
        assert abs(len(out) // 2 - 4800) <= 2

    def test_chunked_equals_one_shot(self) -> None:
        """分块喂入与一次性喂入输出一致（边界连续的核心保证）。"""
        data = pcm_sine(24000, 120)
        one_shot = PcmResampler(24000, 48000).feed(data)
        rs = PcmResampler(24000, 48000)
        step = 960  # 480 样本一块
        chunked = b"".join(rs.feed(data[i:i + step]) for i in range(0, len(data), step))
        assert abs(len(chunked) - len(one_shot)) <= 4
        assert chunked[: len(one_shot) - 4] == one_shot[: len(chunked) - 4] \
            or chunked == one_shot

    def test_upsample_smoothness(self) -> None:
        """重采样输出无块间跳变（相邻样本差值有界）。"""
        import numpy as np
        data = pcm_sine(24000, 200)
        rs = PcmResampler(24000, 48000)
        step = 960  # 20ms 一块
        out = b"".join(rs.feed(data[i:i + step]) for i in range(0, len(data), step))
        arr = np.frombuffer(out, dtype=np.int16).astype(np.int32)
        assert int(np.abs(np.diff(arr)).max()) < 2000

    def test_empty_feed(self) -> None:
        rs = PcmResampler(16000, 48000)
        assert rs.feed(b"") == b""


class TestPlaybackQueue:
    async def test_fifo_order(self) -> None:
        q = PlaybackQueue()
        for i in range(3):
            q.push(PlaybackFrame(pcm=bytes([i]), sample_rate=48000))
        assert (await q.read()).pcm == b"\x00"
        assert (await q.read()).pcm == b"\x01"

    async def test_overflow_sheds_oldest(self) -> None:
        q = PlaybackQueue()
        for i in range(PlaybackQueue._MAX_FRAMES + 10):
            q.push(PlaybackFrame(pcm=bytes([i % 256]), sample_rate=48000))
        first = await q.read()
        assert first is not None and first.pcm != b"\x00"  # 最旧帧已被挤出

    async def test_final_frame_survives_overflow(self) -> None:
        q = PlaybackQueue()
        for i in range(PlaybackQueue._MAX_FRAMES + 10):
            q.push(PlaybackFrame(pcm=bytes([i % 256]), sample_rate=48000))
        q.finish(turn_id=7)
        seen_final = False
        for _ in range(PlaybackQueue._MAX_FRAMES):
            frame = await q.read()
            if frame and frame.final:
                seen_final = True
                assert frame.turn_id == 7
        assert seen_final

    async def test_clear_delivers_interrupt_sentinel(self) -> None:
        q = PlaybackQueue()
        q.push(PlaybackFrame(pcm=b"x", sample_rate=48000))
        q.clear()
        assert await q.read() is None
        assert q.generation == 1
