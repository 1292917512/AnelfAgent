"""实时语音回声内容过滤测试：转写被近期已播文本高度覆盖 = 回声丢弃。

回归自线上实证：AI 播报被扬声器回录成用户轮（「稍等哈。」回声轮），
AI 回应自己的话形成自激循环（用户感知「重复播放 AI 说的话」）。
"""

from __future__ import annotations

import asyncio
import time

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
from test_finalize_feedback import _speak_one_turn

from agent.realtime.engine import RealtimeEngine

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


class TestEchoFilter:
    async def test_echo_turn_discarded(self, app) -> None:
        """转写被近期播报文本覆盖 → 判定回声：rt_final(discarded)，不进思维。"""
        from agent.audio import get_audio_registry
        get_audio_registry().register(_canned_provider("稍等哈。"))
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            engine._note_spoken(session, "稍等哈，我马上查两边的天气～")
            await _speak_one_turn(engine)
            await _wait_for(lambda: any(e[0] == "rt_final" for e in sink.events))
            final = next(e for e in sink.events if e[0] == "rt_final")
            assert final[1]["discarded"] is True
            await asyncio.sleep(0.1)
            assert app.messages == []
        finally:
            await engine.stop("c1")

    async def test_real_speech_delivered(self, app) -> None:
        """与播报无关的真实语音：正常投递（过滤不误伤）。"""
        from agent.audio import get_audio_registry
        get_audio_registry().register(_canned_provider("帮我看看明天天气怎么样"))
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            engine._note_spoken(session, "稍等哈，我马上查两边的天气～")
            await _speak_one_turn(engine)
            await _wait_for(lambda: len(app.messages) == 1)
            assert "明天天气" in app.messages[0]["content"]
        finally:
            await engine.stop("c1")

    async def test_short_interjection_not_filtered(self, app) -> None:
        """短应答（<3 归一字符）天然高重合，永不被过滤。"""
        from agent.audio import get_audio_registry
        get_audio_registry().register(_canned_provider("对"))
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            engine._note_spoken(session, "对，转写就当初稿看～")
            await _speak_one_turn(engine)
            await _wait_for(lambda: len(app.messages) == 1)
        finally:
            await engine.stop("c1")

    async def test_window_expired_not_echo(self, app) -> None:
        """播报超出回溯窗口：不再视为回声。"""
        from agent.audio import get_audio_registry
        get_audio_registry().register(_canned_provider("稍等哈。"))
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            session.spoken_recent.append((time.monotonic() - 999.0, "稍等哈我马上查两边的天气"))
            await _speak_one_turn(engine)
            await _wait_for(lambda: len(app.messages) == 1)
        finally:
            await engine.stop("c1")

    async def test_disabled_config_passthrough(self, app) -> None:
        """开关关闭：回声形态照常投递。"""
        from agent.audio import get_audio_registry
        from core.config import ConfigManager
        ConfigManager.set("realtime_echo_filter_enabled", False)
        try:
            get_audio_registry().register(_canned_provider("稍等哈。"))
            engine = RealtimeEngine()
            sink = FakeSink()
            session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
            try:
                engine._note_spoken(session, "稍等哈，我马上查两边的天气～")
                await _speak_one_turn(engine)
                await _wait_for(lambda: len(app.messages) == 1)
            finally:
                await engine.stop("c1")
        finally:
            ConfigManager.set("realtime_echo_filter_enabled", True)

    async def test_noise_only_discarded_silently(self, app) -> None:
        """纯标点转写（环境碎响误转）：静默丢弃，不进思维、不提示「没听清」。"""
        from agent.audio import get_audio_registry
        get_audio_registry().register(_canned_provider("。"))
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            await _speak_one_turn(engine)
            await _wait_for(lambda: any(e[0] == "rt_final" for e in sink.events))
            final = next(e for e in sink.events if e[0] == "rt_final")
            assert final[1]["discarded"] is True
            assert not any(e[0] == "rt_error" for e in sink.events)
            await asyncio.sleep(0.1)
            assert app.messages == []
        finally:
            await engine.stop("c1")

    async def test_mid_stream_echo_discarded(self, app) -> None:
        """回复流在播（未结算）时的回声：增量文本参与比对，同样丢弃。"""
        from agent.audio import get_audio_registry
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        scope = "user_webui:u1"
        try:
            await _speak_one_turn(engine)
            await _wait_for(lambda: len(app.messages) == 1)
            # 回复流开管（未结算）：增量文本在播
            await engine._on_delta({"scope": scope, "turn_id": "m1",
                                    "delta": "听感上确实会像我在自言自语，"})
            get_audio_registry().register(_canned_provider("我在自言自语。"))
            # 播放中开口：250ms 打断确认是墙钟，喂帧须留出确认窗再收声
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(20):
                await engine.accept_pcm("c1", pcm_tone(20))
            await asyncio.sleep(0.35)
            for _ in range(30):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: any(
                e[0] == "rt_final" and e[1].get("discarded") for e in sink.events))
            await asyncio.sleep(0.1)
            assert len(app.messages) == 1  # 回声未进思维
        finally:
            await engine.stop("c1")


class TestSpokenRecord:
    async def test_reply_stream_recorded_on_settle(self, app) -> None:
        """回复流结算时登记已播文本（回声比对事实源贯通）。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        scope = "user_webui:u1"
        try:
            await _speak_one_turn(engine)
            await _wait_for(lambda: len(app.messages) == 1)
            await engine._on_delta({"scope": scope, "turn_id": "m1", "delta": "你好呀，"})
            await engine._on_delta({"scope": scope, "turn_id": "m1", "delta": "我在听。"})
            await engine._on_after_reply({"scope": scope, "turn_id": "m1"})
            spoken = [text for _, text in session.spoken_recent]
            assert any("你好呀" in text and "我在听" in text for text in spoken)
        finally:
            await engine.stop("c1")

    async def test_proactive_broadcast_recorded(self, app) -> None:
        """主动播报开管即登记（车道排队中也可能已被听到一部分）。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            result = await engine.speak_to_scope("user_webui:u1", "出发记得带伞")
            assert result["spoken"] is True
            spoken = [text for _, text in session.spoken_recent]
            assert "出发记得带伞" in spoken
        finally:
            await engine.stop("c1")
