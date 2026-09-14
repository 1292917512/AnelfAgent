"""音频流解码 — 压缩音频流（MP3 等）→ PCM16 流（ffmpeg 子进程管道）。

TTS 提供者多数产出 MP3 流；播放链只消费 PCM16。本模块用一条
ffmpeg 子进程管道做流式转码：压缩块从 stdin 推入，PCM16 块从 stdout
按块读出（不落地临时文件、不做整段缓冲，首块延迟即 ffmpeg 启动耗时）。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from agent.audio.ffmpeg import ffmpeg_bin

_READ_CHUNK = 8192


async def decode_stream_to_pcm16(
    encoded: AsyncIterator[bytes],
    *,
    sample_rate: int = 24000,
) -> AsyncIterator[bytes]:
    """压缩音频块流 → PCM16 块流（16k/24k/48k 单声道）。

    生产端异常/数据损坏时静默收尾（降级链在上层处理）；调用方负责
    在取消时关闭本生成器（子进程随生成器关闭回收）。
    """
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_bin(), "-v", "error",
        "-i", "pipe:0",
        "-f", "s16le", "-acodec", "pcm_s16le",
        "-ar", str(sample_rate), "-ac", "1",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def _feed() -> None:
        assert proc.stdin is not None
        try:
            async for chunk in encoded:
                proc.stdin.write(chunk)
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass

    feeder = asyncio.create_task(_feed(), name="tts.decode.feed")
    try:
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(_READ_CHUNK)
            if not chunk:
                break
            yield chunk
    finally:
        feeder.cancel()
        try:
            await feeder
        except (asyncio.CancelledError, Exception):
            pass
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
