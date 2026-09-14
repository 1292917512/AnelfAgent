"""MiniMax WebSocket 双工 TTS 组件 — 长连接流式语音合成。

与 HTTP 流式（tts_providers.MiniMaxTtsProvider）互补的第二种传输：
WebSocket 长连接（T2A WS 协议）——连接复用、首块延迟更低、
适合实时对话的高频短句合成；HTTP 流式作为同提供者的另一种形态
同在优先级链上（运行时失败互相降级）。

协议时序：task_start（模型/音色/PCM 24k 参数）→ task_started →
task_continue（文本）→ 流式 task_continue（hex 音频块）→
task_finish → task_finished。
"""

from __future__ import annotations

import base64  # noqa: F401 —— 协议扩展预留（二进制帧形态）
import json
from typing import Any, AsyncIterator, Dict

from entities._sdk import TtsStream

_LOG_TAG = "语音合成"

_WS_URL = "wss://api.minimaxi.com/ws/v1/t2a"


class MiniMaxWsTtsProvider:
    """MiniMax WebSocket TTS（T2A WS 协议，PCM 直出）。"""

    name = "minimax_ws"
    priority = 15

    def __init__(self, ws_factory: Any = None) -> None:
        self._ws_factory = ws_factory

    async def check_available(self) -> bool:
        from entities.minimax.client import MiniMaxClient
        return MiniMaxClient().configured

    def stream_synthesize(
        self, text: str, *, voice: str = "", sample_rate: int = 24000,
    ) -> TtsStream:
        return TtsStream(self._stream(text, voice=voice, sample_rate=sample_rate),
                         sample_rate)

    async def _stream(
        self, text: str, *, voice: str, sample_rate: int,
    ) -> AsyncIterator[bytes]:
        import websockets

        from entities.minimax.client import MiniMaxClient, get_config
        client = MiniMaxClient()
        model = get_config("default_tts_model", "speech-2.8-hd")
        voice_id = voice or get_config("default_voice_id", "male-qn-qingse")

        factory = self._ws_factory or websockets.connect
        ws = await factory(_WS_URL, additional_headers=client._auth_headers())
        try:
            await ws.send(json.dumps({
                "event": "task_start",
                "model": model,
                "voice_setting": {
                    "voice_id": voice_id, "speed": 1.0, "vol": 1.0, "pitch": 0,
                },
                "audio_setting": {"format": "pcm", "sample_rate": sample_rate},
            }))
            started = json.loads(await ws.recv())
            self._check_event(started, expect="task_started")

            await ws.send(json.dumps({"event": "task_continue", "text": text}))
            while True:
                message = json.loads(await ws.recv())
                event = message.get("event")
                if event == "task_continue":
                    audio_hex = (message.get("data") or {}).get("audio", "")
                    if audio_hex:
                        yield bytes.fromhex(audio_hex)
                    if message.get("is_final"):
                        break
                elif event in ("task_failed", "task_finished"):
                    if event == "task_failed":
                        self._raise_status(message)
                    break
            try:
                await ws.send(json.dumps({"event": "task_finish"}))
            except Exception:
                pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    @staticmethod
    def _check_event(message: Dict[str, Any], *, expect: str) -> None:
        if message.get("event") != expect:
            raise RuntimeError(
                f"MiniMax WS 握手失败: {message.get('event') or message}")

    @staticmethod
    def _raise_status(message: Dict[str, Any]) -> None:
        base = message.get("base_resp") or {}
        raise RuntimeError(
            f"MiniMax WS 合成失败: {base.get('status_msg') or message}")
