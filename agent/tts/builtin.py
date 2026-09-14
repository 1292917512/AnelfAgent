"""内置流式 TTS 提供者 — 内部模型链（OpenAI 风格）与 edge-tts 可选组件。

- OpenAI 风格：audio/speech 流式 HTTP（response_format=pcm，24k 直出），
  凭据走模型配置（llm_clients.json 的 tts 类型链）——内部模型利用的内置提供者；
- edge-tts：微软边缘语音（MP3 流经 ffmpeg 管道解码为 PCM16），
  可选第三方库（未安装时 check_available 为 False 自动退出链）。

MiniMax 等云平台提供者以组件形式从实体注册（见 entities/minimax）。
"""

from __future__ import annotations

from typing import AsyncIterator

from agent.tts.decode import decode_stream_to_pcm16
from agent.tts.providers import TtsStream, get_tts_registry
from core.log import log

_LOG_TAG = "语音合成"


class OpenAiTtsProvider:
    """OpenAI 风格流式 TTS（audio/speech，PCM 直出；模型配置 tts 链凭据）。"""

    name = "openai"
    priority = 20

    async def check_available(self) -> bool:
        from agent.llm import get_llm_manager
        return get_llm_manager().get_media_client("tts") is not None

    def stream_synthesize(
        self, text: str, *, voice: str = "", sample_rate: int = 24000,
    ) -> TtsStream:
        return TtsStream(self._stream(text, voice=voice, sample_rate=sample_rate),
                         sample_rate)

    async def _stream(
        self, text: str, *, voice: str, sample_rate: int,
    ) -> AsyncIterator[bytes]:
        from agent.llm import get_llm_manager
        manager = get_llm_manager()
        media = manager.get_media_client("tts")
        if media is None:
            raise RuntimeError("无可用媒体 TTS 凭据")
        model = manager.get_tts_model() or "tts-1"
        payload = {
            "model": model,
            "voice": voice or "alloy",
            "input": text,
            "response_format": "pcm",
        }
        headers = {"Authorization": f"Bearer {media._api_key}",
                   "Content-Type": "application/json"}
        async with media._http_client(timeout=60.0) as http:
            async with http.stream(
                "POST", f"{media._base_url}/audio/speech",
                headers=headers, json=payload,
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(8192):
                    if chunk:
                        yield chunk


class EdgeTtsProvider:
    """edge-tts 流式 TTS（MP3 流经 ffmpeg 管道解码为 PCM16）。"""

    name = "edge_tts"
    priority = 30

    async def check_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def stream_synthesize(
        self, text: str, *, voice: str = "", sample_rate: int = 24000,
    ) -> TtsStream:
        return TtsStream(self._stream(text, voice=voice, sample_rate=sample_rate),
                         sample_rate)

    async def _stream(
        self, text: str, *, voice: str, sample_rate: int,
    ) -> AsyncIterator[bytes]:
        from core.config import get_config
        voice_name = voice or str(get_config("tts_edge_voice", "zh-CN-XiaoxiaoNeural"))

        async def _mp3() -> AsyncIterator[bytes]:
            import edge_tts
            communicate = edge_tts.Communicate(text, voice_name)
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio":
                    yield chunk["data"]

        async for pcm in decode_stream_to_pcm16(_mp3(), sample_rate=sample_rate):
            yield pcm


def register_builtin_tts_providers() -> None:
    """注册内置流式 TTS 提供者（bootstrap 调用一次，同名覆盖幂等）。"""
    registry = get_tts_registry()
    registry.register(OpenAiTtsProvider())
    registry.register(EdgeTtsProvider())
    log("内置流式 TTS 提供者已注册: openai/edge_tts", "DEBUG", tag=_LOG_TAG)
