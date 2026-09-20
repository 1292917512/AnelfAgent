"""千问实时语音方言 — 百炼 WebSocket 直连音频通道（语音到语音）。

协议要点（Qwen-Audio Realtime API，事件模型与 OpenAI Realtime 同构）：
- 连接：wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=...，Bearer 鉴权；
- 会话初始化：session.update（pcm 双端格式——输入 16k / 输出 24k、server_vad
  端点检测、系统指令——人格简述注入）；
- 上行：input_audio_buffer.append（base64 PCM16 16k）；
- 下行：response.audio.delta（base64 PCM16 24k 回复音频）、
  response.audio_transcript.*（回复字幕）、
  conversation.item.input_audio_transcription.*（用户转写，delta 含已确定
  text 与暂存 stash 两段）、input_audio_buffer.speech_started（barge-in
  信号）、response.done；
- 打断：response.cancel；错误分级——invalid_request_error 不断连（记录后
  忽略），server_error 视为通道错误上抛。

凭据走组件凭据中心（provider_keys 的 dashscope 条目 → 环境变量
DASHSCOPE_API_KEY），与实时语音类模块的凭据约定一致，不经大模型配置。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any, AsyncIterator, Dict, Optional

from core.log import log
from core.provider_keys import get_provider_key

from .base import NativeEvent

_LOG_TAG = "实时语音"

_DEFAULT_WS_BASE = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"


class QwenRealtimeClient:
    """千问实时语音通道（一次语音会话一个实例）。"""

    input_rate = 16000
    """Qwen-Audio Realtime 的 PCM16 输入采样率。"""

    def __init__(
        self,
        *,
        model: str = "qwen-audio-3.0-realtime-plus",
        voice: str = "longanqian",
        instructions: str = "",
        api_key: str = "",
        ws_base: str = "",
        ws_factory: Any = None,
    ) -> None:
        self._model = model
        self._voice = voice
        self._instructions = instructions
        if not api_key:
            api_key = get_provider_key("dashscope") or \
                os.environ.get("DASHSCOPE_API_KEY", "").strip()
        self._api_key = api_key
        self._ws_base = ws_base or _DEFAULT_WS_BASE
        self._ws_factory = ws_factory
        self._ws: Any = None
        self._queue: asyncio.Queue[NativeEvent] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        if not self._api_key:
            raise RuntimeError("无百炼凭据（组件凭据 dashscope / 环境变量 DASHSCOPE_API_KEY 均无）")
        import websockets
        url = f"{self._ws_base}?model={self._model}"
        factory = self._ws_factory or websockets.connect
        self._ws = await factory(
            url,
            additional_headers={"Authorization": f"Bearer {self._api_key}"},
        )
        await self._send({
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "voice": self._voice,
                "instructions": self._instructions,
                "input_audio_format": "pcm",
                "output_audio_format": "pcm",
                "turn_detection": {"type": "server_vad"},
            },
        })
        self._reader_task = asyncio.create_task(
            self._reader(), name="rt.native.qwen")

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
        """截断当前回复（barge-in；server_vad 已自动取消时幂等）。"""
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
        elif mtype == "response.audio_transcript.delta":
            await self._queue.put(NativeEvent(
                kind="transcript", role="assistant",
                text=str(message.get("delta", "")), final=False,
            ))
        elif mtype == "response.audio_transcript.done":
            await self._queue.put(NativeEvent(
                kind="transcript", role="assistant",
                text=str(message.get("transcript", "")), final=True,
            ))
        elif mtype == "conversation.item.input_audio_transcription.delta":
            await self._queue.put(NativeEvent(
                kind="transcript", role="user",
                text=str(message.get("text", "")) + str(message.get("stash", "")),
                final=False,
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
            error = message.get("error") or {}
            if str(error.get("type", "")) == "server_error":
                await self._queue.put(NativeEvent(
                    kind="error",
                    message=str(error.get("message", "") or error)))
            else:
                log(f"千问实时通道忽略客户端错误: {error.get('message', '')}",
                    "DEBUG", tag=_LOG_TAG)
