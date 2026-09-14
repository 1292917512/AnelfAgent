"""OpenAI Realtime 方言 — WebSocket 直连音频通道（语音到语音）。

协议要点（Realtime API）：
- 连接：GET /v1/realtime?model=...（wss），Bearer 鉴权 + OpenAI-Beta 头；
- 会话初始化：session.update（pcm16 双端格式、server_vad 端点检测、
  whisper 输入转写、系统指令——人格简述注入）；
- 上行：input_audio_buffer.append（base64 PCM16 24k）；
- 下行：response.audio.delta（base64 PCM16 24k 回复音频）、
  response.audio_transcript.*（回复转写）、
  response.input_audio_transcription.*（用户转写）、
  input_audio_buffer.speech_started（barge-in 信号）、response.done；
- 打断：response.cancel（截断当前回复生成）。
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any, AsyncIterator, Dict, Optional

from .base import NativeEvent, provider_credentials

_LOG_TAG = "实时语音"


class OpenAiRealtimeClient:
    """OpenAI Realtime 通道（一次语音会话一个实例）。"""

    input_rate = 24000
    """Realtime API 的 PCM16 输入采样率。"""

    def __init__(
        self,
        *,
        model: str = "gpt-4o-realtime-preview",
        voice: str = "alloy",
        instructions: str = "",
        api_key: str = "",
        base_url: str = "",
        ws_factory: Any = None,
    ) -> None:
        self._model = model
        self._voice = voice
        self._instructions = instructions
        if not api_key:
            api_key, base_url = provider_credentials(openai=True)
        self._api_key = api_key
        self._base_url = base_url or "https://api.openai.com"
        self._ws_factory = ws_factory
        self._ws: Any = None
        self._queue: asyncio.Queue[NativeEvent] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        if not self._api_key:
            raise RuntimeError("无 OpenAI 凭据（llm_clients 未配置 openai 供应商）")
        import websockets
        url = (self._base_url.replace("https://", "wss://").replace("http://", "ws://")
               + f"/v1/realtime?model={self._model}")
        factory = self._ws_factory or websockets.connect
        self._ws = await factory(
            url,
            additional_headers={
                "Authorization": f"Bearer {self._api_key}",
                "OpenAI-Beta": "realtime=v1",
            },
        )
        await self._send({
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "voice": self._voice,
                "instructions": self._instructions,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {"type": "server_vad"},
            },
        })
        self._reader_task = asyncio.create_task(
            self._reader(), name="rt.native.openai")

    async def _send(self, message: Dict[str, Any]) -> None:
        assert self._ws is not None
        await self._ws.send(json.dumps(message))

    async def send_audio(self, pcm: bytes) -> None:
        if self._ws is None:
            return
        await self._send({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm).decode(),
        })

    def events(self) -> AsyncIterator[NativeEvent]:
        async def _gen() -> AsyncIterator[NativeEvent]:
            while True:
                yield await self._queue.get()
        return _gen()

    async def interrupt(self) -> None:
        """截断当前回复（barge-in；server_vad 已自动 truncate 时幂等）。"""
        if self._ws is not None:
            await self._send({"type": "response.cancel"})

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
        mtype = str(message.get("type", ""))
        if mtype == "response.audio.delta":
            await self._queue.put(NativeEvent(
                kind="audio",
                pcm=base64.b64decode(message.get("delta", "")),
                sample_rate=24000,
            ))
        elif mtype == "response.audio_transcript.done":
            await self._queue.put(NativeEvent(
                kind="transcript", role="assistant",
                text=str(message.get("transcript", "")), final=True,
            ))
        elif mtype == "conversation.item.input_audio_transcription.completed":
            await self._queue.put(NativeEvent(
                kind="transcript", role="user",
                text=str(message.get("transcript", "")), final=True,
            ))
        elif mtype == "input_audio_buffer.speech_started":
            await self._queue.put(NativeEvent(kind="speech_started"))
        elif mtype == "response.done":
            await self._queue.put(NativeEvent(kind="turn_complete"))
        elif mtype == "error":
            await self._queue.put(NativeEvent(
                kind="error", message=str(message.get("error", {}))))
