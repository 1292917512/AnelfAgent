"""Qwen 实时流式 ASR 提供者（entities/audiosync/qwen_asr_stream）单元测试。

锁定协议接线和轮间隔离语义：session.update 形态（manual 提交，无双 VAD）、
append base64 上行、text→partial / completed→final / failed→异常 的事件路由、
一轮一连接（commit 后随轮关闭，杜绝跨轮缓冲串扰）、连接失败冷却降级。
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

from entities.audiosync.qwen_asr_stream import (
    QwenRealtimeAsrProvider,
    _QwenAsrConnection,
    _QwenAsrSession,
)


class _FakeWs:
    """假 WS：send 记录上行帧，feed() 注入下行事件。"""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._inbound: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    def feed(self, message: dict) -> None:
        self._inbound.put_nowait(json.dumps(message))

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return await self._inbound.get()
        except asyncio.CancelledError:
            raise StopAsyncIteration from None


def _make_conn(monkeypatch: pytest.MonkeyPatch) -> tuple[_QwenAsrConnection, _FakeWs, list]:
    ws = _FakeWs()
    failures: list[bool] = []

    async def _connect(url, additional_headers=None, proxy=None, close_timeout=None):
        assert "model=qwen3-asr-flash-realtime" in url
        assert additional_headers == {"Authorization": "Bearer test-key"}
        assert proxy is None  # 公网直连端点显式绕过环境代理（SOCKS 缺 python-socks 会炸）
        assert close_timeout == 1.0  # 收拢握手有界（远端不回 CLOSE 不卡轮末随关）
        return ws

    import websockets
    monkeypatch.setattr(websockets, "connect", _connect)
    conn = _QwenAsrConnection("test-key", lambda: failures.append(True))
    return conn, ws, failures


async def _ready_session(monkeypatch: pytest.MonkeyPatch) -> tuple[_QwenAsrSession, _FakeWs]:
    conn, ws, _ = _make_conn(monkeypatch)
    session = _QwenAsrSession(conn, 16000)
    ready = asyncio.create_task(conn.ensure())
    await asyncio.sleep(0.05)
    ws.feed({"type": "session.updated"})
    await ready
    return session, ws


class TestConnectionLifecycle:
    async def test_session_update_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        conn, ws, _ = _make_conn(monkeypatch)
        ready = asyncio.create_task(conn.ensure())
        await asyncio.sleep(0.05)
        update = next(m for m in ws.sent if m["type"] == "session.update")
        session = update["session"]
        assert session["modalities"] == ["text"]
        assert session["input_audio_format"] == "pcm"
        assert session["sample_rate"] == 16000
        assert session["turn_detection"] is None  # manual 提交：本地端点单一裁决
        ws.feed({"type": "session.updated"})
        await asyncio.wait_for(ready, timeout=2)
        assert conn.alive
        await conn.close()


class TestSessionFlow:
    async def test_append_and_partial(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session, ws = await _ready_session(monkeypatch)
        events = await session.accept_pcm(b"\x01\x00" * 320, 16000)
        appends = [m for m in ws.sent if m["type"] == "input_audio_buffer.append"]
        assert base64.b64decode(appends[0]["audio"]) == b"\x01\x00" * 320
        assert events == []

        ws.feed({"type": "conversation.item.input_audio_transcription.text",
                 "text": "你好", "stash": "，这"})
        await asyncio.sleep(0.05)  # reader 泵路由
        events = await session.accept_pcm(b"\x02\x00" * 320, 16000)
        assert len(events) == 1 and events[0].kind == "partial"
        assert events[0].text == "你好，这"

    async def test_commit_returns_final_and_closes_connection(
            self, monkeypatch: pytest.MonkeyPatch) -> None:
        session, ws = await _ready_session(monkeypatch)
        await session.accept_pcm(b"\x01\x00" * 320, 16000)

        commit_task = asyncio.create_task(session.commit())
        await asyncio.sleep(0.05)
        assert any(m["type"] == "input_audio_buffer.commit" for m in ws.sent)
        ws.feed({"type": "conversation.item.input_audio_transcription.completed",
                 "transcript": "你好世界"})
        events = await asyncio.wait_for(commit_task, timeout=2)
        assert len(events) == 1 and events[0].kind == "final"
        assert events[0].text == "你好世界"
        assert ws.closed, "一轮一连接：commit 后必须随轮关闭（防跨轮缓冲串扰）"

    async def test_commit_failure_raises_and_closes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        session, ws = await _ready_session(monkeypatch)
        await session.accept_pcm(b"\x01\x00" * 320, 16000)
        commit_task = asyncio.create_task(session.commit())
        await asyncio.sleep(0.05)
        ws.feed({"type": "conversation.item.input_audio_transcription.failed",
                 "error": {"message": "upstream overloaded"}})
        with pytest.raises(RuntimeError, match="upstream overloaded"):
            await asyncio.wait_for(commit_task, timeout=2)
        assert ws.closed

    async def test_commit_timeout_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import entities.audiosync.qwen_asr_stream as mod
        monkeypatch.setattr(mod, "_COMMIT_TIMEOUT_SECONDS", 0.2)
        session, ws = await _ready_session(monkeypatch)
        await session.accept_pcm(b"\x01\x00" * 320, 16000)
        with pytest.raises(asyncio.TimeoutError):
            await session.commit()
        assert ws.closed


class TestProviderGating:
    async def test_unavailable_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import entities.audiosync.qwen_asr_stream as mod
        monkeypatch.setattr(mod, "_resolve_api_key", lambda ws_base: "")
        provider = QwenRealtimeAsrProvider()
        assert await provider.check_available() is False

    async def test_unavailable_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import entities.audiosync.qwen_asr_stream as mod
        monkeypatch.setattr(mod, "get_config_bool", lambda *a, **k: False)
        provider = QwenRealtimeAsrProvider()
        assert await provider.check_available() is False

    async def test_cooldown_after_connect_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """连接失败后冷却期内 check_available=False（让位链上后续提供者）。"""
        provider = QwenRealtimeAsrProvider()
        provider._note_failure()
        assert await provider.check_available() is False

    async def test_per_session_independent_connections(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """每轮会话独立连接：两轮之间无共享缓冲（防幻影重复轮）。"""
        conns: list[_FakeWs] = []

        async def _connect(url, additional_headers=None, proxy=None):
            ws = _FakeWs()
            conns.append(ws)
            return ws

        import websockets
        monkeypatch.setattr(websockets, "connect", _connect)
        provider = QwenRealtimeAsrProvider()
        s1 = provider.open_session(16000)
        s2 = provider.open_session(16000)
        assert s1._conn is not s2._conn
