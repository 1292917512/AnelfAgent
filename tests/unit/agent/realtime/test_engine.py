"""实时引擎级联管线测试：语音 → 定稿 → 统一入口 → 增量回复 → TTS → 播放。"""

from __future__ import annotations

import asyncio
import json
import math

import pytest

from agent.realtime.engine import RealtimeEngine
from agent.realtime.session import RealtimeSink, SessionState
from agent.tts import get_tts_registry
from agent.tts.providers import TtsStream
from agent.voice.session import VoiceDelivery

RATE = 16000


def pcm_silence(ms: int) -> bytes:
    return b"\x00\x00" * int(RATE * ms / 1000)


def pcm_tone(ms: int, amp: int = 8000) -> bytes:
    n = int(RATE * ms / 1000)
    frames = bytearray()
    for i in range(n):
        v = int(amp * math.sin(2 * math.pi * 440 * i / RATE))
        frames += v.to_bytes(2, "little", signed=True)
    return bytes(frames)


class FakeStreamAsrSession:
    def __init__(self) -> None:
        self.pcm = bytearray()

    async def accept_pcm(self, pcm: bytes, sample_rate: int):
        self.pcm.extend(pcm)
        return []

    async def commit(self):
        from agent.audio.streaming import AsrEvent
        return [AsrEvent(kind="final", text="你好世界", segments=[])]

    async def close(self):
        return []


class FakeStreamAsrProvider:
    name = "fake_stream"
    kind = "asr_stream"
    priority = 1

    async def check_available(self) -> bool:
        return True

    def open_session(self, sample_rate: int = 16000):
        return FakeStreamAsrSession()


class FakeTtsProvider:
    name = "fake_tts"
    priority = 1

    async def check_available(self) -> bool:
        return True

    def stream_synthesize(self, text: str, *, voice: str = "", sample_rate: int = 24000):
        async def _chunks():
            yield b"\x01\x02" * 240
        return TtsStream(_chunks(), 24000)


class FakeApp:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


class FakeSink:
    def __init__(self) -> None:
        self.audio: list[tuple[bytes, int]] = []
        self.events: list[tuple[str, dict]] = []

    def as_sink(self) -> RealtimeSink:
        async def _audio(pcm: bytes, rate: int) -> None:
            self.audio.append((pcm, rate))

        async def _event(name: str, payload: dict) -> None:
            self.events.append((name, payload))

        return RealtimeSink(send_audio=_audio, send_event=_event)


@pytest.fixture(autouse=True)
def clean_registries(monkeypatch):
    from agent.audio import get_audio_registry
    saved_audio = get_audio_registry().list()
    saved_tts = get_tts_registry().list()
    get_audio_registry().reset()
    get_tts_registry().reset()
    get_audio_registry().register(FakeStreamAsrProvider())
    get_tts_registry().register(FakeTtsProvider())
    yield
    get_audio_registry().reset()
    get_tts_registry().reset()
    for p in saved_audio:
        get_audio_registry().register(p)
    for p in saved_tts:
        get_tts_registry().register(p)


@pytest.fixture
def app(monkeypatch):
    fake = FakeApp()
    monkeypatch.setattr(
        "agent.runtime.agent_app.get_agent_app", lambda: fake, raising=False)
    return fake


async def _wait_for(cond, timeout: float = 3.0) -> None:
    async def _poll():
        while not cond():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(_poll(), timeout)


def _delivery() -> VoiceDelivery:
    return VoiceDelivery(user_id="u1", user_name="用户", session_id="", adapter_key="webui")


class TestCascadeFlow:
    async def test_full_turn(self, app) -> None:
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            # 底噪学习 → 语音 → 静音收束
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))

            # 定稿事件 + 用户消息经统一入口
            await _wait_for(lambda: any(e[0] == "rt_final" for e in sink.events))
            final = next(e for e in sink.events if e[0] == "rt_final")
            assert final[1]["text"] == "你好世界"
            await _wait_for(lambda: len(app.messages) == 1)
            assert app.messages[0]["content"] == "你好世界"

            # 模拟思维增量回复 → TTS → 下行音频
            from core.event_bus import EVENT_AFTER_REPLY, event_bus
            from core.stream_events import EVENT_ASSISTANT_DELTA
            scope = engine._scope_of(session)
            await event_bus.emit(EVENT_ASSISTANT_DELTA, {
                "scope": scope, "turn_id": "m1", "delta": "你好呀。"})
            await event_bus.emit(EVENT_AFTER_REPLY, {"scope": scope})
            await _wait_for(lambda: any(e[0] == "audio_done" for e in sink.events))
            assert sink.audio, "TTS 音频应已下行"
            assert all(rate == 48000 for _, rate in sink.audio)
            done = next(e for e in sink.events if e[0] == "audio_done")
            assert done[1]["interrupted"] is False
            assert session.state is SessionState.LISTENING
        finally:
            await engine.stop("c1")

    async def test_barge_in_interrupts_playback(self, app) -> None:
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", _delivery(), sink.as_sink(), RATE)
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

            # 用户打断：新一轮语音 → interrupted 收束 + 回到收听
            old_turn = session.turn_id
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            await _wait_for(lambda: any(
                e[0] == "audio_done" and e[1].get("interrupted") for e in sink.events))
            assert session.turn_id > old_turn
            assert session.state is SessionState.LISTENING
        finally:
            await engine.stop("c1")

    async def test_lease_conflict_rejected(self, app) -> None:
        from agent.voice.session import VoiceLeaseBusy
        engine = RealtimeEngine()
        await engine.start("c1", _delivery(), FakeSink().as_sink(), RATE)
        try:
            with pytest.raises(VoiceLeaseBusy):
                await engine.start("c1", _delivery(), FakeSink().as_sink(), RATE)
        finally:
            await engine.stop("c1")

    async def test_frames_without_session_dropped(self, app) -> None:
        engine = RealtimeEngine()
        await engine.accept_pcm("ghost", pcm_tone(20))  # 不抛错即正确


class TestReadinessAndTurns:
    async def test_start_rejected_without_asr(self, app) -> None:
        """无 ASR 提供者：cascade 启动显式拒绝（不再静默失败）。"""
        from agent.audio import get_audio_registry
        from agent.tts import get_tts_registry
        get_audio_registry().reset()
        get_tts_registry().reset()
        get_tts_registry().register(FakeTtsProvider())
        engine = RealtimeEngine()
        with pytest.raises(RuntimeError, match="ASR"):
            await engine.start("c1", _delivery(), FakeSink().as_sink(), RATE)
        assert engine.status()["owners"] == []

    async def test_warns_when_no_tts(self, app) -> None:
        """无 TTS 提供者：会话可开（文字回复），但下行有 rt_error 警告。"""
        from agent.tts import get_tts_registry
        get_tts_registry().reset()
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            warn = next(e for e in sink.events if e[0] == "rt_error")
            assert "TTS" in warn[1]["message"]
        finally:
            await engine.stop("c1")

    async def test_tool_only_reply_returns_to_listening(self, app) -> None:
        """纯工具轮（无增量文本）：after_reply 后状态回到收听（不卡思考中）。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: len(app.messages) == 1)
            assert session.state is SessionState.THINKING
            from core.event_bus import EVENT_AFTER_REPLY, event_bus
            await event_bus.emit(EVENT_AFTER_REPLY, {"scope": engine._scope_of(session)})
            await _wait_for(lambda: session.state is SessionState.LISTENING)
        finally:
            await engine.stop("c1")

    async def test_final_and_reply_share_turn(self, app) -> None:
        """rt_final 与后续回复音频同属一轮（turn 在语音起始时开启）。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: any(e[0] == "rt_final" for e in sink.events))
            final = next(e for e in sink.events if e[0] == "rt_final")
            assert final[1]["turn_id"] == 1
            from core.event_bus import EVENT_AFTER_REPLY, event_bus
            from core.stream_events import EVENT_ASSISTANT_DELTA
            scope = engine._scope_of(engine._sessions["c1"])
            await event_bus.emit(EVENT_ASSISTANT_DELTA, {
                "scope": scope, "turn_id": "m1", "delta": "你好呀。"})
            await event_bus.emit(EVENT_AFTER_REPLY, {"scope": scope})
            await _wait_for(lambda: any(e[0] == "audio_done" for e in sink.events))
            done = next(e for e in sink.events if e[0] == "audio_done")
            assert done[1]["turn_id"] == 1
        finally:
            await engine.stop("c1")


class TestCrossModeLease:
    async def test_utterance_rejected_during_realtime(self, app) -> None:
        """实时通话中开成段录音 → 租约拒绝（麦克风单会话）。"""
        import agent.realtime.engine as engine_mod
        from services.voice import VoiceLeaseBusy, get_voice_service
        engine = RealtimeEngine()
        await engine.start("c1", _delivery(), FakeSink().as_sink(), RATE)
        try:
            engine_mod._engine = engine  # 门面读单例
            with pytest.raises(VoiceLeaseBusy):
                await get_voice_service().start("c1", sample_rate=RATE, user_id="u1")
        finally:
            engine_mod._engine = None
            await engine.stop("c1")


class TestRealtimeSayTool:
    async def test_say_rejected_without_session(self) -> None:
        import json

        import agent.realtime.tools as tools_mod
        body = json.loads(await tools_mod.realtime_say("你好"))
        assert "error" in body

    async def test_say_rejected_when_busy(self, app) -> None:
        """用户轮进行中主动开口 → 可重试错误（不切断用户轮）。"""
        import json

        import agent.realtime.tools as tools_mod
        engine = RealtimeEngine()
        engine_mod = __import__("agent.realtime.engine", fromlist=["engine"])
        sink = FakeSink()
        session = await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            engine_mod._engine = engine
            await session.set_state(SessionState.SPEAKING)
            body = json.loads(await tools_mod.realtime_say("插句话"))
            assert "error" in body
            assert body["retryable"] is True
        finally:
            engine_mod._engine = None
            await engine.stop("c1")

    async def test_say_when_idle(self, app) -> None:
        """空闲时主动开口：TTS 合成并播放，状态进 SPEAKING。"""
        import json

        import agent.realtime.tools as tools_mod
        engine = RealtimeEngine()
        engine_mod = __import__("agent.realtime.engine", fromlist=["engine"])
        sink = FakeSink()
        await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            engine_mod._engine = engine
            body = json.loads(await tools_mod.realtime_say("主动说一句。"))
            assert body.get("success") is True
            await _wait_for(lambda: any(e[0] == "audio_done" for e in sink.events))
            assert sink.audio
        finally:
            engine_mod._engine = None
            await engine.stop("c1")


class TestSilentFailurePaths:
    async def test_empty_transcript_sends_discarded_final(self, app) -> None:
        """未识别到有效语音：rt_final(discarded) 收帧，状态保持收听。"""
        engine = RealtimeEngine()
        sink = FakeSink()

        class _EmptySession(FakeStreamAsrSession):
            async def commit(self):
                from agent.audio.streaming import AsrEvent
                return [AsrEvent(kind="final", text="", segments=[])]

        class _EmptyProvider(FakeStreamAsrProvider):
            def open_session(self, sample_rate: int = 16000):
                return _EmptySession()

        from agent.audio import get_audio_registry
        get_audio_registry().register(_EmptyProvider())
        session = await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: any(e[0] == "rt_final" for e in sink.events))
            final = next(e for e in sink.events if e[0] == "rt_final")
            assert final[1]["discarded"] is True
            assert session.state is SessionState.LISTENING
            assert app.messages == []
        finally:
            await engine.stop("c1")

    async def test_send_message_failure_recovers(self, app) -> None:
        """思维投递失败：rt_error 提示 + 状态回收（不断连不卡死）。"""
        async def _boom(**kwargs):
            raise RuntimeError("runtime not ready")
        app.send_message = _boom  # type: ignore[method-assign]
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: any(e[0] == "rt_error" for e in sink.events))
            await _wait_for(lambda: session.state is SessionState.LISTENING)
        finally:
            await engine.stop("c1")

    async def test_reply_error_warns_user(self, app) -> None:
        """思维轮出错：rt_error 提示而非无声回到收听。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: len(app.messages) == 1)
            from core.event_bus import EVENT_AFTER_REPLY, event_bus
            await event_bus.emit(EVENT_AFTER_REPLY, {
                "scope": engine._scope_of(session), "error": "llm_error"})
            await _wait_for(lambda: any(e[0] == "rt_error" for e in sink.events))
            await _wait_for(lambda: session.state is SessionState.LISTENING)
        finally:
            await engine.stop("c1")

    async def test_sentence_timeout_skips(self, app) -> None:
        """TTS 提供者挂起：句级超时跳过，后续流程不拖死。"""
        from agent.tts import get_tts_registry
        from core.config import ConfigManager

        class _HangProvider(FakeTtsProvider):
            name = "hang"
            priority = 0

            def stream_synthesize(self, text, *, voice="", sample_rate=24000):
                async def _chunks():
                    import asyncio as _a
                    await _a.sleep(60)
                    yield b""
                return TtsStream(_chunks(), 24000)

        get_tts_registry().reset()
        get_tts_registry().register(_HangProvider())
        ConfigManager.set("tts_sentence_timeout_s", 1)
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", _delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: len(app.messages) == 1)
            from core.event_bus import EVENT_AFTER_REPLY, event_bus
            from core.stream_events import EVENT_ASSISTANT_DELTA
            scope = engine._scope_of(engine._sessions["c1"])
            await event_bus.emit(EVENT_ASSISTANT_DELTA, {
                "scope": scope, "turn_id": "m1", "delta": "你好呀。"})
            await event_bus.emit(EVENT_AFTER_REPLY, {"scope": scope})
            await _wait_for(
                lambda: any(e[0] == "audio_done" for e in sink.events), timeout=8.0)
            assert any(e[0] == "rt_error" for e in sink.events)
        finally:
            ConfigManager.set("tts_sentence_timeout_s", 20)
            await engine.stop("c1")


class TestPreprocessWiring:
    async def test_mic_frames_pass_preprocessor(self, app) -> None:
        """级联管线的麦克风帧先过预处理链再进端点检测/ASR。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c9", _delivery(), sink.as_sink(), RATE)
        try:
            seen: list[bytes] = []
            original = session.preprocessor.feed

            def _recording_feed(pcm: bytes) -> bytes:
                seen.append(pcm)
                return original(pcm)

            session.preprocessor.feed = _recording_feed  # type: ignore[method-assign]
            for _ in range(10):
                await engine.accept_pcm("c9", pcm_silence(20))
            assert len(seen) == 10
            assert all(frame == pcm_silence(20) for frame in seen)
        finally:
            await engine.stop("c9")


class TestRealtimeReplyTool:
    async def test_reply_without_session_rejected(self, app) -> None:
        from agent.realtime.tools import realtime_reply

        out = json.loads(await realtime_reply("你好"))
        assert out.get("success") is not True or "没有进行中的实时通话" in out.get("detail", out.get("hint", "")) or out.get("error") or "通话" in str(out)

    async def test_say_and_reply_accept_voice(self, app) -> None:
        """voice 参数经音色解析透传（_resolve_voice）。"""
        from agent.realtime import tools as rt_tools

        assert rt_tools._resolve_voice(" taffy_voice_0805 ") == "taffy_voice_0805"
        from core.config import ConfigManager
        ConfigManager.set("realtime_tts_voice", "longanhuan_v3.6")
        assert rt_tools._resolve_voice("") == "longanhuan_v3.6"
        ConfigManager.set("realtime_tts_voice", "")


class TestCallContextInjection:
    async def test_no_session_no_injection(self, app) -> None:
        from agent.realtime.context import RealtimeCallProvider

        snap = await RealtimeCallProvider().provide("user_webui:web_user")
        assert snap is None

    async def test_active_session_injects_discipline(self, app) -> None:
        from agent.realtime.context import RealtimeCallProvider
        from agent.realtime.engine import get_realtime_engine

        engine = get_realtime_engine()
        sink = FakeSink()
        await engine.start("c-ctx", _delivery(), sink.as_sink(), RATE)
        try:
            snap = await RealtimeCallProvider().provide("user_webui:web_user")
            assert snap is not None and "realtime_reply" in snap.content
            assert "send_message" in snap.content
        finally:
            await engine.stop("c-ctx")
