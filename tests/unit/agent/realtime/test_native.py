"""原生实时方言测试：OpenAI Realtime / Gemini Live 协议编解码（假 WS）。"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from agent.realtime.native.gemini import GeminiLiveClient
from agent.realtime.native.openai import OpenAiRealtimeClient


class FakeWs:
    """假 WebSocket：记录发送、脚本化接收。"""

    def __init__(self, incoming: list[dict]):
        self.sent: list[dict] = []
        self._incoming = asyncio.Queue()
        for message in incoming:
            self._incoming.put_nowait(json.dumps(message))
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return self._incoming.get_nowait()
        except asyncio.QueueEmpty:
            raise StopAsyncIteration from None

    async def close(self) -> None:
        self.closed = True


def _factory(ws: FakeWs):
    async def _connect(url: str, **kwargs):
        ws.url = url
        ws.kwargs = kwargs
        return ws
    return _connect


async def _collect(client, count: int):
    events = []
    async for event in client.events():
        events.append(event)
        if len(events) >= count:
            break
    return events


class TestOpenAiRealtime:
    async def test_connect_sends_session_update(self) -> None:
        ws = FakeWs([])
        client = OpenAiRealtimeClient(
            api_key="sk-test", base_url="https://api.openai.com",
            ws_factory=_factory(ws))
        await client.connect()
        assert "wss://api.openai.com/v1/realtime?model=" in ws.url
        assert ws.kwargs["additional_headers"]["Authorization"] == "Bearer sk-test"
        update = ws.sent[0]
        assert update["type"] == "session.update"
        assert update["session"]["input_audio_format"] == "pcm16"

    async def test_send_audio_appends_base64(self) -> None:
        ws = FakeWs([])
        client = OpenAiRealtimeClient(api_key="sk-test", ws_factory=_factory(ws))
        await client.connect()
        await client.send_audio(b"\x01\x02" * 100)
        append = ws.sent[-1]
        assert append["type"] == "input_audio_buffer.append"
        assert base64.b64decode(append["audio"]) == b"\x01\x02" * 100

    async def test_event_dispatch(self) -> None:
        pcm = b"\x10\x20" * 240
        ws = FakeWs([
            {"type": "response.audio.delta",
             "delta": base64.b64encode(pcm).decode()},
            {"type": "conversation.item.input_audio_transcription.completed",
             "transcript": "你好"},
            {"type": "input_audio_buffer.speech_started"},
            {"type": "response.done"},
        ])
        client = OpenAiRealtimeClient(api_key="sk-test", ws_factory=_factory(ws))
        await client.connect()
        events = await _collect(client, 4)
        assert events[0].kind == "audio" and events[0].pcm == pcm
        assert events[1].kind == "transcript" and events[1].role == "user"
        assert events[2].kind == "speech_started"
        assert events[3].kind == "turn_complete"

    async def test_interrupt_sends_cancel(self) -> None:
        ws = FakeWs([])
        client = OpenAiRealtimeClient(api_key="sk-test", ws_factory=_factory(ws))
        await client.connect()
        await client.interrupt()
        assert ws.sent[-1]["type"] == "response.cancel"

    async def test_missing_credentials_rejected(self) -> None:
        client = OpenAiRealtimeClient(api_key="")
        with pytest.raises(RuntimeError):
            await client.connect()


class TestGeminiLive:
    async def test_connect_sends_setup(self) -> None:
        ws = FakeWs([])
        client = GeminiLiveClient(api_key="gm-test", ws_factory=_factory(ws))
        await client.connect()
        assert "key=gm-test" in ws.url
        setup = ws.sent[0]["setup"]
        assert setup["generation_config"]["response_modalities"] == ["AUDIO"]

    async def test_send_audio_media_chunk(self) -> None:
        ws = FakeWs([])
        client = GeminiLiveClient(api_key="gm-test", ws_factory=_factory(ws))
        await client.connect()
        await client.send_audio(b"\x01\x02" * 100)
        chunk = ws.sent[-1]["realtime_input"]["media_chunks"][0]
        assert chunk["mime_type"] == "audio/pcm;rate=16000"
        assert base64.b64decode(chunk["data"]) == b"\x01\x02" * 100

    async def test_event_dispatch(self) -> None:
        pcm = b"\x10\x20" * 240
        ws = FakeWs([
            {"serverContent": {"modelTurn": {"parts": [
                {"inlineData": {"mimeType": "audio/pcm;rate=24000",
                                "data": base64.b64encode(pcm).decode()}}]}}},
            {"serverContent": {"inputTranscription": {"text": "你好"}}},
            {"serverContent": {"interrupted": True}},
            {"serverContent": {"turnComplete": True}},
        ])
        client = GeminiLiveClient(api_key="gm-test", ws_factory=_factory(ws))
        await client.connect()
        events = await _collect(client, 4)
        assert events[0].kind == "audio" and events[0].pcm == pcm
        assert events[1].kind == "transcript" and events[1].role == "user"
        assert events[2].kind == "speech_started"
        assert events[3].kind == "turn_complete"
