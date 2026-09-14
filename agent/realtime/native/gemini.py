"""Gemini Live 方言 — WebSocket 直连音频通道（语音到语音）。

协议要点（Live API / BidiGenerateContent）：
- 连接：wss generativelanguage...BidiGenerateContent?key=...；
- 会话初始化：setup（模型、AUDIO 响应模态、预建音色、输入/输出转写）；
- 上行：realtime_input.media_chunks（audio/pcm;rate=16000，base64）；
- 下行：serverContent.modelTurn.parts.inlineData（audio/pcm;rate=24000
  回复音频）、inputTranscription/outputTranscription（双侧转写）、
  serverContent.interrupted（提供方判定的打断信号）、turnComplete；
- 打断：提供方自动截断（interrupted 事件即确认），无需显式取消。
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, AsyncIterator, Dict, Optional

from .base import NativeEvent, provider_credentials

_LOG_TAG = "实时语音"

_WS_URL = ("wss://generativelanguage.googleapis.com/ws/"
           "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")


class GeminiLiveClient:
    """Gemini Live 通道（一次语音会话一个实例）。"""

    input_rate = 16000
    """Live API 的 PCM16 输入采样率。"""

    def __init__(
        self,
        *,
        model: str = "gemini-2.0-flash-live-001",
        voice: str = "Aoede",
        instructions: str = "",
        api_key: str = "",
        base_url: str = "",
        ws_factory: Any = None,
    ) -> None:
        self._model = model
        self._voice = voice
        self._instructions = instructions
        if not api_key:
            api_key, _ = provider_credentials(openai=False)
        self._api_key = api_key
        self._base_url = base_url or _WS_URL
        self._ws_factory = ws_factory
        self._ws: Any = None
        self._queue: asyncio.Queue[NativeEvent] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        if not self._api_key:
            raise RuntimeError("无 Gemini 凭据（llm_clients 未配置 google 供应商）")
        import websockets
        factory = self._ws_factory or websockets.connect
        self._ws = await factory(f"{self._base_url}?key={self._api_key}")
        await self._send({
            "setup": {
                "model": f"models/{self._model}",
                "generation_config": {
                    "response_modalities": ["AUDIO"],
                    "speech_config": {
                        "voice_config": {
                            "prebuilt_voice_config": {"voice_name": self._voice},
                        },
                    },
                },
                "system_instruction": {"parts": [{"text": self._instructions}]},
                "input_audio_transcription": {},
                "output_audio_transcription": {},
            },
        })
        self._reader_task = asyncio.create_task(
            self._reader(), name="rt.native.gemini")

    async def _send(self, message: Dict[str, Any]) -> None:
        assert self._ws is not None
        await self._ws.send(json.dumps(message))

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is None:
            return
        await self._send({
            "realtime_input": {
                "media_chunks": [{
                    "mime_type": f"audio/pcm;rate={self.input_rate}",
                    "data": base64.b64encode(pcm).decode(),
                }],
            },
        })

    def events(self) -> AsyncIterator[NativeEvent]:
        async def _gen() -> AsyncIterator[NativeEvent]:
            while True:
                yield await self._queue.get()
        return _gen()

    async def interrupt(self) -> None:
        """截断当前回复（Live API 自动截断，显式清空输入缓冲辅助对齐）。"""
        # BidiGenerateContent 无显式取消消息；提供方检测语音起始自动 interrupted
        return

    async def close(self) -> None:
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _reader(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._queue.put(NativeEvent(kind="error", message=str(exc)))

    async def _dispatch(self, message: Dict[str, Any]) -> None:
        server = message.get("serverContent") or message.get("server_content") or {}
        if not isinstance(server, dict):
            return
        if server.get("interrupted"):
            await self._queue.put(NativeEvent(kind="speech_started"))
        model_turn = server.get("modelTurn") or server.get("model_turn") or {}
        for part in model_turn.get("parts", []) if isinstance(model_turn, dict) else []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            data = inline.get("data")
            if data:
                await self._queue.put(NativeEvent(
                    kind="audio", pcm=base64.b64decode(data), sample_rate=24000,
                ))
        input_tx = server.get("inputTranscription") or server.get("input_transcription")
        if isinstance(input_tx, dict) and input_tx.get("text"):
            await self._queue.put(NativeEvent(
                kind="transcript", role="user", text=str(input_tx["text"]), final=True,
            ))
        output_tx = server.get("outputTranscription") or server.get("output_transcription")
        if isinstance(output_tx, dict) and output_tx.get("text"):
            await self._queue.put(NativeEvent(
                kind="transcript", role="assistant",
                text=str(output_tx["text"]), final=True,
            ))
        if server.get("turnComplete") or server.get("turn_complete"):
            await self._queue.put(NativeEvent(kind="turn_complete"))
