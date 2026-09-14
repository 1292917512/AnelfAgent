"""语音会话管理器测试：租约、端点检测、成段交付、连接清理。"""

from __future__ import annotations

import asyncio
import struct
import wave

import pytest

from agent.voice import VoiceLeaseBusy, VoiceSessionManager, voice_sink_port
from core.audio_frames import AudioFrame


@pytest.fixture(autouse=True)
def _fast_vad(monkeypatch, tmp_path):
    """加速端点检测参数 + WAV 落盘重定向到临时目录。"""
    from core.config import ConfigManager
    ConfigManager.set("voice_silence_ms", 200)
    ConfigManager.set("voice_min_utterance_ms", 50)
    ConfigManager.set("voice_max_utterance_s", 2)
    ConfigManager.set("voice_vad_floor_min", 100)
    monkeypatch.setattr(
        "core.path.ConfigPaths.UPLOAD_DIR", str(tmp_path), raising=False,
    )
    yield


@pytest.fixture
def manager():
    m = VoiceSessionManager()
    yield m
    m.reset()


@pytest.fixture
def sink(manager):
    """经 LateBinding 端口施绑伪 sink（生产由 wiring.py 施绑 deliver_utterance）。"""
    received: list = []

    async def _sink(utterance):
        received.append(utterance)

    voice_sink_port.set(_sink)
    yield received
    voice_sink_port.unbind()


def _frame(value: int, n_samples: int = 480, rate: int = 48000) -> AudioFrame:
    # 交流方波（恒定值是直流，会被端点检测的 DC 阻断正确滤除）
    pcm = struct.pack(
        f"<{n_samples}h",
        *([value if i % 2 else -value for i in range(n_samples)]),
    )
    return AudioFrame(
        sample_rate=rate, pcm=pcm, duration_ms=n_samples / rate * 1000,
    )


LOUD = _frame(3000)
SILENT = _frame(0)


def _read_wav(path: str) -> tuple[int, int]:
    with wave.open(path, "rb") as wf:
        return wf.getframerate(), wf.getnframes()


class TestLease:
    async def test_second_session_rejected(self, manager):
        manager.start_session("o1", "conn-a", 48000)
        with pytest.raises(VoiceLeaseBusy):
            manager.start_session("o1", "conn-b", 48000)

    async def test_lease_reusable_after_end(self, manager, sink):
        manager.start_session("o1", "conn-a", 48000)
        await manager.end_session("o1", "conn-a")
        manager.start_session("o1", "conn-b", 48000)  # 不抛

    def test_owns(self, manager):
        manager.start_session("o1", "conn-a", 48000)
        assert manager.owns("o1", "conn-a")
        assert not manager.owns("o1", "conn-b")
        assert not manager.owns("o2", "conn-a")

    async def test_foreign_frames_dropped(self, manager, sink):
        """无租约连接的帧静默丢弃，不成段。"""
        manager.start_session("o1", "conn-a", 48000)
        await manager.accept_frame("o1", "conn-b", LOUD)
        await manager.end_session("o1", "conn-a")
        assert sink == []


class TestEndpointing:
    async def test_silence_finalizes_utterance(self, manager, sink):
        manager.start_session("o1", "conn-a", 48000)
        for _ in range(30):  # 300ms 有声
            await manager.accept_frame("o1", "conn-a", LOUD)
        for _ in range(25):  # 250ms 静音 > 200ms 阈值 → 收束
            await manager.accept_frame("o1", "conn-a", SILENT)
        assert len(sink) == 1
        u = sink[0]
        assert u.owner == "o1"
        assert u.sample_rate == 48000
        assert u.duration_ms >= 300
        rate, frames = _read_wav(u.file_path)
        assert rate == 48000
        # 预处理链按跳距定帧输出（滞留 ≤ 一个帧长，收束前冲刷补齐）
        assert 480 * 50 <= frames <= 480 * 55 + 1536

    async def test_short_utterance_discarded(self, manager, sink):
        """低于最短时长的段视为误触发丢弃。"""
        manager.start_session("o1", "conn-a", 48000)
        await manager.accept_frame("o1", "conn-a", LOUD)  # 10ms < 50ms
        for _ in range(25):
            await manager.accept_frame("o1", "conn-a", SILENT)
        assert sink == []

    async def test_max_duration_force_cuts(self, manager, sink):
        manager.start_session("o1", "conn-a", 48000)
        for _ in range(210):  # 2.1s > 2s 上限 → 强制切段
            await manager.accept_frame("o1", "conn-a", LOUD)
        assert len(sink) == 1
        assert sink[0].duration_ms >= 2000

    async def test_end_session_flushes_pending(self, manager, sink):
        manager.start_session("o1", "conn-a", 48000)
        for _ in range(30):
            await manager.accept_frame("o1", "conn-a", LOUD)
        await manager.end_session("o1", "conn-a")
        assert len(sink) == 1

    async def test_watchdog_finalizes_without_new_frames(self, manager, sink):
        """最后一帧之后无新帧，看门狗按静音阈值兜底收束。"""
        manager.start_session("o1", "conn-a", 48000)
        for _ in range(30):
            await manager.accept_frame("o1", "conn-a", LOUD)
        await asyncio.sleep(0.6)  # silence(200ms) + 宽限(200ms)
        assert len(sink) == 1


class TestConnectionCleanup:
    async def test_drop_connection_flushes_owned_sessions(self, manager, sink):
        manager.start_session("o1", "conn-a", 48000)
        manager.start_session("o2", "conn-b", 48000)
        for _ in range(30):
            await manager.accept_frame("o1", "conn-a", LOUD)
            await manager.accept_frame("o2", "conn-b", LOUD)
        await manager.drop_connection("conn-a")
        assert len(sink) == 1
        assert sink[0].owner == "o1"
        assert manager.owns("o2", "conn-b")  # 其他连接不受影响


class TestDeliver:
    """deliver_utterance：语音段经 AgentApp 统一入口转 Everything 进消息管线。"""

    async def test_delivers_voice_segment_via_agent_app(self, monkeypatch):
        from agent.channel.schemas import SegmentType
        from agent.voice.deliver import deliver_utterance
        from agent.voice.session import VoiceDelivery, VoiceUtterance

        sent: list = []

        class _FakeApp:
            async def send_message(self, **kwargs):
                sent.append(kwargs)

        monkeypatch.setattr(
            "agent.runtime.agent_app.get_agent_app", lambda: _FakeApp(),
        )
        utterance = VoiceUtterance(
            owner="conn-1", file_path="/tmp/v.wav", sample_rate=48000,
            duration_ms=1200.0, started_at=0.0,
            delivery=VoiceDelivery(
                user_id="web_user", user_name="用户",
                session_id="c1", adapter_key="webui",
            ),
        )
        await deliver_utterance(utterance)

        assert len(sent) == 1
        call = sent[0]
        assert call["user_id"] == "web_user"
        assert call["adapter_key"] == "webui"
        assert call["session_id"] == "c1"
        assert call["to_me"] is True
        seg = call["media_segments"][0]
        assert seg.type == SegmentType.VOICE
        assert seg.file_path == "/tmp/v.wav"
        assert seg.duration == 1.2

    async def test_unbound_sink_port_is_fail_open(self, monkeypatch, tmp_path, manager):
        """端口未施绑时成段只广播事件不投递（fail-open 不炸会话）。"""
        # 只解绑本端口并在事后恢复（reset_all 会波及无关端口的测试隔离）
        saved = voice_sink_port.get() if voice_sink_port.bound else None
        voice_sink_port.unbind()
        try:
            manager.start_session("o1", "conn-a", 48000)
            for _ in range(30):
                await manager.accept_frame("o1", "conn-a", LOUD)
            await manager.end_session("o1", "conn-a")  # 不抛即通过
        finally:
            if saved is not None:
                voice_sink_port.set(saved)


class TestPreprocessWiring:
    async def test_frames_pass_preprocessor(self, manager, sink, monkeypatch):
        """段会话的输入帧先过预处理链再入缓冲/端点检测。"""
        from agent.voice import session as voice_session

        seen: list[bytes] = []

        class Recorder:
            def feed(self, pcm: bytes) -> bytes:
                seen.append(pcm)
                return pcm

            def reset(self) -> None:
                pass

        monkeypatch.setattr(voice_session, "create_preprocessor", lambda rate: Recorder())
        manager.start_session("o1", "conn-a", 48000)
        await manager.accept_frame("o1", "conn-a", LOUD)
        assert seen == [LOUD.pcm]
