"""百炼流式语音合成组件 — CosyVoice / Qwen-TTS（PCM 直出）。

SpeechSynthesizer 双向流式：streaming_call 提交文本、回调线程吐 PCM
块，经 call_soon_threadsafe 桥入事件循环产出 TtsStream（PCM16 字节块
异步迭代器）。播放链的边界重采样把任意源率适配到播放率，源率固定
24k（SDK PCM 高质量档）；cancel 走 streaming_cancel（连接可复用语义
由 SDK 保证，句级管线每句新建连接同样成立）。

模型 dashscope_tts_model（qwen-audio-3.0-tts-plus / cosyvoice-v3.5-plus
…），默认音色 dashscope_tts_voice，优先级 dashscope_tts_priority。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from core.config import ConfigManager

from . import sdk

_SENTINEL_DONE = b""
_SENTINEL_ERROR = b"\x00__error__"


class _SynthCallback:
    """SDK 回调：音频块/错误桥入事件循环队列。"""

    def __init__(self, put) -> None:
        self._put = put

    def on_data(self, data: bytes) -> None:
        self._put(data or b" ")

    def on_error(self, message: str) -> None:
        self._put(_SENTINEL_ERROR + str(message).encode("utf-8", "replace"))

    def on_complete(self) -> None:
        self._put(_SENTINEL_DONE)

    def on_close(self) -> None:
        self._put(_SENTINEL_DONE)

    def on_open(self) -> None:
        pass

    def on_event(self, message: str) -> None:
        pass


class DashScopeTtsProvider:
    """流式合成提供者（核心 TTS 注册表，实时通话与句级管线共用）。"""

    name = "dashscope"
    unavailable_hint = "百炼语音未就绪：未装 dashscope SDK 或未解析到 API Key"

    @property
    def priority(self) -> int:
        return int(ConfigManager.get("dashscope_tts_priority", 20) or 20)

    async def check_available(self) -> bool:
        return sdk.sdk_ready()

    def stream_synthesize(self, text: str, *, voice: str = "", sample_rate: int = 24000):
        from entities._sdk import TtsStream

        return TtsStream(self._chunks(text, voice, sample_rate), 24000)

    async def _chunks(
        self, text: str, voice: str, sample_rate: int,
    ) -> AsyncIterator[bytes]:
        ds, reason = sdk.import_sdk()
        if ds is None:
            raise RuntimeError(reason)
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

        fmt = AudioFormat.PCM_16000HZ_MONO_16BIT if sample_rate == 16000 \
            else AudioFormat.PCM_24000HZ_MONO_16BIT
        model = str(ConfigManager.get("dashscope_tts_model", "qwen-audio-3.0-tts-plus"))
        if not voice:
            voice = str(ConfigManager.get("dashscope_tts_voice", "longanhuan_v3.6"))

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        put = sdk.thread_to_loop(loop, queue)
        from typing import Any, cast

        synthesizer = SpeechSynthesizer(
            model=model, voice=voice, format=fmt,
            callback=cast(Any, _SynthCallback(put)))
        await sdk.run_sync(synthesizer.streaming_call, text)
        await sdk.run_sync(synthesizer.streaming_complete)
        error: str = ""
        while True:
            item = await queue.get()
            if item == _SENTINEL_DONE:
                break
            if item.startswith(_SENTINEL_ERROR):
                error = item[len(_SENTINEL_ERROR):].decode("utf-8", "replace")
                continue
            yield item
        if error:
            raise RuntimeError(f"百炼合成失败: {error}")
