"""MiniMax WS TTS 组件测试（假 WS 协议时序）+ barge-in 回声防护测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from entities.minimax.ws_tts import MiniMaxWsTtsProvider


class FakeWs:
    def __init__(self, incoming: list[dict]):
        self.sent: list[dict] = []
        self._incoming = asyncio.Queue()
        for message in incoming:
            self._incoming.put_nowait(json.dumps(message))
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        return await self._incoming.get()

    async def close(self) -> None:
        self.closed = True


def _factory(ws: FakeWs):
    async def _connect(url: str, **kwargs):
        ws.url = url
        return ws
    return _connect


class TestMiniMaxWsTts:
    async def test_handshake_and_stream(self, monkeypatch) -> None:
        pcm1, pcm2 = b"\x01\x02" * 100, b"\x03\x04" * 100
        ws = FakeWs([
            {"event": "task_started"},
            {"event": "task_continue", "data": {"audio": pcm1.hex()}},
            {"event": "task_continue", "data": {"audio": pcm2.hex()}, "is_final": True},
        ])
        from entities.minimax import client as client_mod
        monkeypatch.setattr(client_mod, "get_config",
                            lambda k, d="": {"api_key": "k"}.get(k, d))
        provider = MiniMaxWsTtsProvider(ws_factory=_factory(ws))
        stream = provider.stream_synthesize("你好", sample_rate=24000)
        chunks = [c async for c in stream.chunks]
        assert chunks == [pcm1, pcm2]
        # 时序：task_start → task_continue → task_finish
        events = [m["event"] for m in ws.sent]
        assert events == ["task_start", "task_continue", "task_finish"]
        assert ws.sent[0]["audio_setting"] == {"format": "pcm", "sample_rate": 24000}
        assert ws.closed is True

    async def test_handshake_failure_raises(self, monkeypatch) -> None:
        ws = FakeWs([{"event": "task_failed",
                      "base_resp": {"status_code": 1001, "status_msg": "鉴权失败"}}])
        from entities.minimax import client as client_mod
        monkeypatch.setattr(client_mod, "get_config",
                            lambda k, d="": {"api_key": "k"}.get(k, d))
        provider = MiniMaxWsTtsProvider(ws_factory=_factory(ws))
        stream = provider.stream_synthesize("你好")
        with pytest.raises(RuntimeError, match="握手失败|鉴权失败"):
            _ = [c async for c in stream.chunks]

    async def test_task_failed_raises(self, monkeypatch) -> None:
        ws = FakeWs([
            {"event": "task_started"},
            {"event": "task_failed",
             "base_resp": {"status_code": 2001, "status_msg": "余额不足"}},
        ])
        from entities.minimax import client as client_mod
        monkeypatch.setattr(client_mod, "get_config",
                            lambda k, d="": {"api_key": "k"}.get(k, d))
        provider = MiniMaxWsTtsProvider(ws_factory=_factory(ws))
        stream = provider.stream_synthesize("你好")
        with pytest.raises(RuntimeError, match="余额不足"):
            _ = [c async for c in stream.chunks]


class TestBargeInEchoGuard:
    async def test_brief_echo_does_not_interrupt(self, app, clean_registries) -> None:
        """播放中短促碎响（< 确认窗）按回声丢弃：播放继续，不产生用户轮。"""
        from rt_fakes import RATE, FakeSink, make_delivery, pcm_silence, pcm_tone

        from agent.realtime.engine import RealtimeEngine
        from core.config import ConfigManager
        ConfigManager.set("realtime_barge_in_onset_ms", 200)
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            # 首轮：语音 → 收束 → 思维 → 开始说话
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            from rt_fakes import wait_for as _wait_for
            await _wait_for(lambda: len(app.messages) == 1)
            from core.event_bus import event_bus
            from core.stream_events import EVENT_ASSISTANT_DELTA
            scope = engine._scope_of(session)
            await event_bus.emit(EVENT_ASSISTANT_DELTA, {
                "scope": scope, "turn_id": "m1", "delta": "开始说话了。"})
            from agent.realtime.session import SessionState
            await _wait_for(lambda: session.state is SessionState.SPEAKING)
            old_turn = session.turn_id

            # 碎响：80ms 语音即收（短于 200ms 确认窗）
            for _ in range(4):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await asyncio.sleep(0.3)

            assert session.turn_id == old_turn  # 未打断
            assert session.state is SessionState.SPEAKING
            assert not any(e[0] == "rt_final" and e[1].get("turn_id", 0) > old_turn
                           for e in sink.events)  # 碎响未成轮
        finally:
            ConfigManager.set("realtime_barge_in_onset_ms", 250)
            await engine.stop("c1")

    async def test_sustained_speech_interrupts_after_window(
        self, app, clean_registries,
    ) -> None:
        """播放中持续开口（> 确认窗）：确认后打断并开启新轮。"""
        from rt_fakes import (
            RATE,
            FakeSink,
            make_delivery,
            pcm_silence,
            pcm_tone,
        )
        from rt_fakes import wait_for as _wait_for

        from agent.realtime.engine import RealtimeEngine
        from agent.realtime.session import SessionState
        from core.config import ConfigManager
        ConfigManager.set("realtime_barge_in_onset_ms", 200)
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: len(app.messages) == 1)
            from core.event_bus import event_bus
            from core.stream_events import EVENT_ASSISTANT_DELTA
            scope = engine._scope_of(session)
            await event_bus.emit(EVENT_ASSISTANT_DELTA, {
                "scope": scope, "turn_id": "m1", "delta": "开始说话了。"})
            await _wait_for(lambda: session.state is SessionState.SPEAKING)
            old_turn = session.turn_id

            # 持续开口（400ms > 确认窗 200ms）
            for _ in range(20):
                await engine.accept_pcm("c1", pcm_tone(20))
            await _wait_for(lambda: session.turn_id > old_turn)
            assert session.state is SessionState.LISTENING
            assert any(e[0] == "audio_done" and e[1].get("interrupted")
                       for e in sink.events)
        finally:
            ConfigManager.set("realtime_barge_in_onset_ms", 250)
            await engine.stop("c1")
