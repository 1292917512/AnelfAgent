"""FunASR 流式 ASR 组件 — 滚动窗部分转写 + 收束定稿。

实现形态（基于既有 FunASR HTTP 服务的增量封装）：
- partial：每 audiosync_stream_step_ms 把最近 audiosync_stream_window_ms
  的语音尾部送一次转写，拼出"截至目前的部分文本"（窗口滚动，部分文本
  随语音推进逐步逼近定稿）；
- final：端点收束（引擎在 VAD 判段时调 commit）→ 整段缓冲送一次完整
  转写，产出带时间戳/声纹向量的结构化分段；缓冲随后清零开始下一段。

滚动窗 HTTP 开销由 step/window 配置控制（默认 600ms/4000ms）；
部分转写失败静默跳过（不阻断语音流），定稿失败抛给引擎降级整段 ASR。
"""

from __future__ import annotations

import time
from typing import Any, List

from core.config import get_config_int
from core.log import log
from entities._sdk import AsrEvent, register_audio_provider

_LOG_TAG = "音源同步"


class _FunAsrStreamSession:
    """一次连续语音的流式转写会话。"""

    def __init__(self, sample_rate: int, transcribe) -> None:
        self._sample_rate = sample_rate
        self._transcribe = transcribe
        self._buf = bytearray()
        self._last_partial_at = 0.0
        self._closed = False

    def _step_bytes(self) -> int:
        return int(self._sample_rate * 2 * get_config_int("audiosync_stream_step_ms", 600) / 1000)

    def _window_bytes(self) -> int:
        return int(self._sample_rate * 2 * get_config_int("audiosync_stream_window_ms", 4000) / 1000)

    async def accept_pcm(self, pcm: bytes, sample_rate: int) -> List[AsrEvent]:
        if self._closed:
            return []
        self._buf.extend(pcm)
        now = time.monotonic()
        if now - self._last_partial_at < get_config_int("audiosync_stream_step_ms", 600) / 1000:
            return []
        self._last_partial_at = now
        # 滚动窗部分转写（仅尾部窗口，失败静默——partial 只是展示辅助）
        window = bytes(self._buf[-self._window_bytes():])
        if not window:
            return []
        try:
            text = await self._transcribe(window, sample_rate, partial=True)
        except Exception as exc:
            log(f"部分转写失败（跳过）: {exc}", "DEBUG", tag=_LOG_TAG)
            return []
        return [AsrEvent(kind="partial", text=text)] if text else []

    async def commit(self) -> List[AsrEvent]:
        """端点收束：整段缓冲完整转写 → final 事件（含结构化分段）。"""
        if not self._buf:
            return []
        buffer = bytes(self._buf)
        self._buf.clear()
        text, segments = await self._transcribe(buffer, self._sample_rate, partial=False)
        return [AsrEvent(kind="final", text=text, segments=segments)]

    async def close(self) -> List[AsrEvent]:
        self._closed = True
        return await self.commit()


class FunAsrStreamingAsrProvider:
    """FunASR 流式 ASR 提供者（asr_stream 类别）。"""

    name = "funasr_stream"
    kind = "asr_stream"
    priority = 10

    async def check_available(self) -> bool:
        from .client import is_configured
        return is_configured()

    def open_session(self, sample_rate: int = 16000) -> _FunAsrStreamSession:
        return _FunAsrStreamSession(sample_rate, self._transcribe_pcm)

    async def _transcribe_pcm(
        self, pcm: bytes, sample_rate: int, *, partial: bool,
    ) -> Any:
        """PCM → WAV → FunASR HTTP 转写；partial 只取拼接文本，定稿取分段。"""
        from . import client
        wav_path, cleanup = await self._pcm_to_wav(pcm, sample_rate)
        try:
            segments = await client.transcribe(wav_path)
        finally:
            if cleanup:
                import os
                try:
                    os.unlink(wav_path)
                except OSError:
                    pass
        if partial:
            return " ".join(s["text"].strip() for s in segments if s["text"].strip())
        text = " ".join(s["text"].strip() for s in segments if s["text"].strip())
        return text, segments

    @staticmethod
    async def _pcm_to_wav(pcm: bytes, sample_rate: int) -> tuple[str, bool]:
        """PCM16 → 临时 WAV 文件（FunASR HTTP 的输入形态）。"""
        import asyncio
        import os
        import tempfile
        import wave

        def _write() -> str:
            fd, path = tempfile.mkstemp(prefix="audiosync_stream_", suffix=".wav")
            with os.fdopen(fd, "wb") as f:
                with wave.open(f, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(pcm)
            return path

        return await asyncio.to_thread(_write), True


def register_streaming_provider() -> None:
    """向核心音频注册表注册流式 ASR 组件（实体包导入时调用一次）。"""
    register_audio_provider(FunAsrStreamingAsrProvider())
