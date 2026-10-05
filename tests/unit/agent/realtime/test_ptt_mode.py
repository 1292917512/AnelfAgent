"""点按通话（PTT）模式测试：按键裁决轮次边界、收发双门控、立即打断、
模式切换、重挂模式应用（与自由通话共享同一收束/投递管线）。
"""

from __future__ import annotations

import asyncio

import pytest
from rt_fakes import (
    FakeSink,
    FakeStreamAsrProvider,
    FakeStreamAsrSession,
    make_delivery,
    pcm_silence,
    pcm_tone,
)
from test_engine import _wait_for

from agent.realtime.engine import RealtimeEngine
from agent.realtime.session import CallMode, SessionState

RATE = 16000

pytestmark = pytest.mark.usefixtures("clean_registries")


def _canned_provider(text: str) -> FakeStreamAsrProvider:
    """定稿文本固定的 ASR 提供者（同名覆盖 fake_stream，对齐既有测试模式）。"""

    class _CannedSession(FakeStreamAsrSession):
        async def commit(self):
            from agent.audio.streaming import AsrEvent
            return [AsrEvent(kind="final", text=text, segments=[])]

    class _CannedProvider(FakeStreamAsrProvider):
        def open_session(self, sample_rate: int = 16000):
            return _CannedSession()

    return _CannedProvider()


async def _ptt_turn(engine: RealtimeEngine, frames: int = 30, conn: str = "c1") -> None:
    """一次完整的点按轮：按下 → 喂若干语音帧 → 松开。"""
    await engine.ptt_press(conn)
    for _ in range(frames):
        await engine.accept_pcm(conn, pcm_tone(20))
    await engine.ptt_release(conn)


class TestPttTurns:
    async def test_press_frames_release_delivers(self, app) -> None:
        """按下→喂帧→松开：正常收束投递（与自由模式同一管线）。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start(
            "c1", make_delivery(), sink.as_sink(), RATE, call_mode=CallMode.PTT)
        try:
            await _ptt_turn(engine)
            await _wait_for(lambda: len(app.messages) == 1)
            assert "你好世界" in app.messages[0]["content"]
            assert session.turn_id == 1
        finally:
            await engine.stop("c1")

    async def test_frames_without_press_ignored(self, app) -> None:
        """未按下时上行帧完全忽略：不开轮、不缓冲、无事件。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start(
            "c1", make_delivery(), sink.as_sink(), RATE, call_mode=CallMode.PTT)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            await asyncio.sleep(0.1)
            assert session.turn_id == 0
            assert bytes(session.pcm_buffer) == b""
            assert app.messages == []
            # 仅初始状态帧，无任何转写事件
            assert all(e[0] == "rt_state" for e in sink.events)
        finally:
            await engine.stop("c1")

    async def test_pause_while_holding_not_split(self, app) -> None:
        """按住期间的停顿不截断：语音-停顿-语音=一轮一次定稿。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start(
            "c1", make_delivery(), sink.as_sink(), RATE, call_mode=CallMode.PTT)
        try:
            await engine.ptt_press("c1")
            for _ in range(20):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(30):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(20):
                await engine.accept_pcm("c1", pcm_tone(20))
            await engine.ptt_release("c1")
            await _wait_for(lambda: len(app.messages) == 1)
            assert session.turn_id == 1
        finally:
            await engine.stop("c1")

    async def test_short_press_discarded(self, app) -> None:
        """短按误触（低于最短有效语音时长）：静默丢弃，不进思维。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start(
            "c1", make_delivery(), sink.as_sink(), RATE, call_mode=CallMode.PTT)
        try:
            await _ptt_turn(engine, frames=10)  # 200ms < 300ms 下限
            await _wait_for(lambda: any(e[0] == "rt_final" for e in sink.events))
            final = next(e for e in sink.events if e[0] == "rt_final")
            assert final[1]["discarded"] is True
            await asyncio.sleep(0.1)
            assert app.messages == []
        finally:
            await engine.stop("c1")


class TestPttInterrupt:
    async def test_press_during_playback_interrupts_immediately(self, app) -> None:
        """播放中按下=立即打断（按钮即发言权，无确认窗等待）。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start(
            "c1", make_delivery(), sink.as_sink(), RATE, call_mode=CallMode.PTT)
        scope = "user_webui:u1"
        try:
            await _ptt_turn(engine)
            await _wait_for(lambda: len(app.messages) == 1)
            await engine._on_delta({"scope": scope, "turn_id": "m1",
                                    "delta": "这段回复正在播放中"})
            await _wait_for(lambda: session.state is SessionState.SPEAKING)
            await engine.ptt_press("c1")  # 立即打断（无 250ms 确认窗）
            assert session.state is SessionState.LISTENING
            await _wait_for(lambda: any(
                e[0] == "audio_done" and e[1].get("interrupted")
                for e in sink.events))
            # 已播残段进回声事实源（打断语义继承）
            assert any("这段回复正在播放中" in text
                       for _, text in session.spoken_recent)
            for _ in range(30):
                await engine.accept_pcm("c1", pcm_tone(20))
            await engine.ptt_release("c1")
            await _wait_for(lambda: len(app.messages) == 2)
        finally:
            await engine.stop("c1")


class TestCallModeSwitch:
    async def test_ptt_to_free_while_pressed_releases_turn(self, app) -> None:
        """按住中切自由模式：按松开收束当前轮，之后恢复检测器流。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start(
            "c1", make_delivery(), sink.as_sink(), RATE, call_mode=CallMode.PTT)
        try:
            await engine.ptt_press("c1")
            for _ in range(30):
                await engine.accept_pcm("c1", pcm_tone(20))
            await engine.set_call_mode("c1", CallMode.FREE)
            assert session.call_mode is CallMode.FREE
            assert session.ptt_active is False
            await _wait_for(lambda: len(app.messages) == 1)
        finally:
            await engine.stop("c1")

    async def test_free_to_ptt_mid_speech_finalizes(self, app) -> None:
        """自由模式说话中切点按：按当前帧收束，检测器复位不滞留。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(30):
                await engine.accept_pcm("c1", pcm_tone(20))
            assert session.detector.in_speech
            await engine.set_call_mode("c1", CallMode.PTT)
            assert session.call_mode is CallMode.PTT
            assert not session.detector.in_speech
            await _wait_for(lambda: len(app.messages) == 1)
        finally:
            await engine.stop("c1")


class TestCallModeLifecycle:
    async def test_start_carries_mode(self, app) -> None:
        """start 带模式：会话初始即点按态。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start(
            "c1", make_delivery(), sink.as_sink(), RATE, call_mode=CallMode.PTT)
        try:
            assert session.call_mode is CallMode.PTT
        finally:
            await engine.stop("c1")

    async def test_reattach_applies_new_call_mode(self, app) -> None:
        """断线重挂：会话接续（轮次保留），模式以本次呼叫为准。"""
        engine = RealtimeEngine()
        sink1 = FakeSink()
        session = await engine.start(
            "c1", make_delivery(), sink1.as_sink(), RATE, call_mode=CallMode.PTT)
        await _ptt_turn(engine)
        await _wait_for(lambda: len(app.messages) == 1)
        old_turn = session.turn_id
        await engine.handle_disconnect("c1")
        sink2 = FakeSink()
        session2 = await engine.start(
            "c2", make_delivery(), sink2.as_sink(), RATE, call_mode=CallMode.FREE)
        try:
            assert session2 is session  # 同会话重挂
            assert session2.call_mode is CallMode.FREE  # 模式以新呼叫为准
            assert session2.turn_id == old_turn  # 轮次保留
        finally:
            await engine.stop("c2")
