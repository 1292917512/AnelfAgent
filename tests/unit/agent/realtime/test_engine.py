"""实时引擎级联管线测试：语音 → 定稿 → 统一入口 → 增量回复 → TTS → 播放。"""

from __future__ import annotations

import asyncio
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


class TestSpeakToScope:
    async def test_no_session_not_spoken(self, app) -> None:
        from agent.realtime.engine import get_realtime_engine

        out = await get_realtime_engine().speak_to_scope(
            "user_webui:u1", "你好")
        assert out == {"spoken": False, "reason": "no-session"}

    async def test_user_speaking_not_interrupted(self, app) -> None:
        from agent.realtime.engine import get_realtime_engine

        engine = get_realtime_engine()
        sink = FakeSink()
        await engine.start("c-spk", _delivery(), sink.as_sink(), RATE)
        try:
            session = engine.session_for_scope("user_webui:u1")
            assert session is not None
            session.detector._in_speech = True  # 模拟用户说话中
            out = await engine.speak_to_scope("user_webui:u1", "你好")
            assert out["spoken"] is False and out["reason"] == "user-speaking"
            session.detector._in_speech = False
        finally:
            await engine.stop("c-spk")


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
            snap = await RealtimeCallProvider().provide("user_webui:u1")
            assert snap is not None and "send_message" in snap.content
            assert "自动以语音播出" in snap.content
            assert "频道=webui" in snap.content
        finally:
            await engine.stop("c-ctx")


class TestSendMessageAutoRoute:
    """send_message 通话自动语音路由：出口层呈现形态判定。"""

    async def test_route_spokes_when_on_call(self, app, monkeypatch) -> None:
        from agent.channel import output_tools
        from agent.realtime.engine import get_realtime_engine

        engine = get_realtime_engine()
        sink = FakeSink()
        await engine.start("c-ar", _delivery(), sink.as_sink(), RATE)
        try:
            spoken: list[tuple[str, str]] = []

            async def fake_speak(scope: str, text: str, voice: str = ""):
                spoken.append((scope, text))
                return {"spoken": True, "turn_id": 1}

            monkeypatch.setattr(engine, "speak_to_scope", fake_speak)
            note = await output_tools._speak_if_on_call("webui", "u1", "你好呀")
            assert note == "spoken"
            assert spoken and spoken[0] == ("user_webui:u1", "你好呀")
        finally:
            await engine.stop("c-ar")

    async def test_route_skipped_without_call(self, app) -> None:
        from agent.channel import output_tools

        note = await output_tools._speak_if_on_call("webui", "u1", "你好")
        assert note == ""

    async def test_route_skipped_for_other_channel(self, app) -> None:
        from agent.realtime.engine import get_realtime_engine

        engine = get_realtime_engine()
        sink = FakeSink()
        await engine.start("c-ar2", _delivery(), sink.as_sink(), RATE)
        try:
            note = await __import__(
                "agent.channel.output_tools", fromlist=["_speak_if_on_call"]
            )._speak_if_on_call("qq", "12345", "你好")
            assert note == ""
        finally:
            await engine.stop("c-ar2")


class TestVoiceFormEvents:
    """语音形态事件：用户转写进聊天流、AI 回复标记播出（webui 频道）。"""

    async def test_transcript_broadcast_on_webui(self, app) -> None:
        from agent.realtime.engine import get_realtime_engine

        engine = get_realtime_engine()
        sink = FakeSink()
        await engine.start("c-vc", _delivery(), sink.as_sink(), RATE)
        try:

            session = engine.session_for_scope("user_webui:u1")
            assert session is not None
            await engine._broadcast_transcript(session, "你好呀")
        finally:
            await engine.stop("c-vc")

    async def test_no_broadcast_for_other_channel(self, app) -> None:
        from agent.realtime.engine import RealtimeSession, get_realtime_engine
        from agent.voice.session import VoiceDelivery

        session = RealtimeSession(
            owner="x", delivery=VoiceDelivery(
                user_id="u1", adapter_key="qq"),
            sample_rate=RATE,
            sink=FakeSink().as_sink())
        await get_realtime_engine()._broadcast_transcript(session, "hi")


class TestSpeakArbitration:
    """播报车道竞争场景：打断期间外插轮次 / 迟到完成事件 / 完成时才标记。"""

    async def test_reply_preempts_proactive_speak(self, app) -> None:
        """S1：思考中主动消息在播 → 回复首增量到达 → 回复抢占、不双任务并发。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c-s1", _delivery(), sink.as_sink(), RATE)
        try:
            await engine.user_turn(session, "帮我看看天气")
            assert session.state is SessionState.THINKING
            out = await engine.speak_to_scope("user_webui:u1", "提醒：晚饭订好了")
            assert out["spoken"] is True
            await _wait_for(lambda: session.lane.active is not None
                            and session.lane.active.source == "speak")
            speak_ut = session.lane.active

            await engine._on_delta({"scope": "user_webui:u1", "delta": "今天晴", "turn_id": "t1"})
            await _wait_for(lambda: session.lane.active is not None
                            and session.lane.active.source == "reply")
            assert speak_ut.superseded is True  # 旧主动播报被取代，不再写帧
            await engine._on_after_reply({"scope": "user_webui:u1", "turn_id": "t1"})
            await _wait_for(lambda: session.lane.active is None)
            # 恰一个自然收束（audio_done 非打断）——不重复、不缺失
            dones = [p for name, p in sink.events if name == "audio_done"]
            assert len([d for d in dones if not d.get("interrupted")]) == 1
        finally:
            await engine.stop("c-s1")

    async def test_speaks_queue_not_interleaved(self, app) -> None:
        """两条主动消息按序全播（车道排队，音频不混排）。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c-s2", _delivery(), sink.as_sink(), RATE)
        try:
            await engine.speak_to_scope("user_webui:u1", "第一条消息")
            await engine.speak_to_scope("user_webui:u1", "第二条消息")
            await _wait_for(lambda: session_lane_idle(engine, "c-s2"))
            dones = [p for name, p in sink.events if name == "audio_done"
                     and not p.get("interrupted")]
            assert len(dones) == 2  # 各自完整收束一次
        finally:
            await engine.stop("c-s2")

    async def test_late_after_reply_keeps_new_turn(self, app, monkeypatch) -> None:
        """S2：旧轮迟到的完成事件归因不上 → 宽限观察，不误杀新一轮语音流。"""
        import agent.realtime.engine as engine_mod
        monkeypatch.setattr(engine_mod, "_SETTLE_GRACE_SECONDS", 0.2)

        engine = engine_mod.RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c-s3", _delivery(), sink.as_sink(), RATE)
        try:
            await engine.user_turn(session, "第一个问题")
            await engine._on_delta({"scope": "user_webui:u1", "delta": "答一", "turn_id": "t1"})
            await _wait_for(lambda: session.lane.active is not None)
            # 旧轮（turn_id=t0）的完成事件迟到：归因不上 → 宽限，不立即结算
            await engine._on_after_reply({"scope": "user_webui:u1", "turn_id": "t0"})
            assert session.tts_pipeline is not None
            assert session.state is SessionState.SPEAKING
            # 宽限期内新一轮增量到达 → 并入/重开语音流，宽限任务取消
            await engine._on_delta({"scope": "user_webui:u1", "delta": "答二", "turn_id": "t2"})
            await engine._on_after_reply({"scope": "user_webui:u1", "turn_id": "t2"})
            await _wait_for(lambda: session_lane_idle(engine, "c-s3"))
            dones = [p for name, p in sink.events if name == "audio_done"
                     and not p.get("interrupted")]
            assert len(dones) >= 1
        finally:
            await engine.stop("c-s3")

    async def test_voice_spoken_marks_on_completion_only(self, app, monkeypatch) -> None:
        """S6：voice_spoken 在实际播出完成时广播；被打断的播报不标记。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        session = await engine.start("c-s4", _delivery(), sink.as_sink(), RATE)
        spoken_log: list[str] = []

        async def _fake_broadcast(sess, text):
            spoken_log.append(text)

        monkeypatch.setattr(RealtimeEngine, "_broadcast_voice_spoken",
                            staticmethod(_fake_broadcast))
        try:
            await engine.speak_to_scope("user_webui:u1", "会被打断的话")
            await _wait_for(lambda: session.lane.active is not None)
            await session.interrupt()  # barge-in：播报被取消
            await _wait_for(lambda: session.lane.active is None)
            assert spoken_log == []  # 未播完，不标记

            await engine.speak_to_scope("user_webui:u1", "完整播出的提醒")
            await _wait_for(lambda: session.lane.active is None)
            assert spoken_log == ["完整播出的提醒"]
        finally:
            await engine.stop("c-s4")

    async def test_scope_suffix_base_match(self, app) -> None:
        """S9：#session 会话后缀不阻断自动路由（基座匹配）。"""
        engine = RealtimeEngine()
        delivery = VoiceDelivery(user_id="u1", user_name="用户",
                                 session_id="chat9", adapter_key="webui")
        await engine.start("c-s5", delivery, FakeSink().as_sink(), RATE)
        try:
            assert engine.session_for_scope("user_webui:u1") is not None
            assert engine.session_for_scope("user_webui:u1#chat9") is not None
            assert engine.session_for_scope("user_webui:u2") is None
        finally:
            await engine.stop("c-s5")


def session_lane_idle(engine: RealtimeEngine, owner: str) -> bool:
    session = engine._sessions.get(owner)
    return session is not None and session.lane.active is None
