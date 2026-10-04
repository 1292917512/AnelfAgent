"""实时语音收束反馈与入库接线测试：转写中间态、空定稿反馈、
声纹向量回填 ingest（流式 ASR 不产分段/向量时音源库不断档）、录音留存接线。
"""

from __future__ import annotations

import pytest
from rt_fakes import FakeSink, FakeStreamAsrProvider, FakeStreamAsrSession, make_delivery
from test_engine import _wait_for, pcm_silence, pcm_tone

from agent.realtime.engine import RealtimeEngine

RATE = 16000

pytestmark = pytest.mark.usefixtures("clean_registries")


class _EmptySession(FakeStreamAsrSession):
    async def commit(self):
        from agent.audio.streaming import AsrEvent
        return [AsrEvent(kind="final", text="", segments=[])]


class _EmptyProvider(FakeStreamAsrProvider):
    def open_session(self, sample_rate: int = 16000):
        return _EmptySession()


async def _speak_one_turn(engine: RealtimeEngine, conn: str = "c1") -> None:
    """喂一段完整语音（静音 → 合成音 → 静音收束），驱动能量法端点检测。"""
    for _ in range(50):
        await engine.accept_pcm(conn, pcm_silence(20))
    for _ in range(50):
        await engine.accept_pcm(conn, pcm_tone(20))
    for _ in range(60):
        await engine.accept_pcm(conn, pcm_silence(20))


class TestFinalizeFeedback:
    async def test_finalizing_event_precedes_final(self, app) -> None:
        """真实语音收束：先发 rt_finalizing（前端转写中），再发 rt_final。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            await _speak_one_turn(engine)
            await _wait_for(lambda: any(e[0] == "rt_final" for e in sink.events))
            names = [e[0] for e in sink.events]
            assert "rt_finalizing" in names
            assert names.index("rt_finalizing") < names.index("rt_final")
        finally:
            await engine.stop("c1")

    async def test_empty_final_sends_info_feedback(self, app) -> None:
        """真实语音定稿为空：rt_final(discarded) + rt_error(info) 提示重说。"""
        from agent.audio import get_audio_registry
        get_audio_registry().register(_EmptyProvider())
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            await _speak_one_turn(engine)
            await _wait_for(lambda: any(e[0] == "rt_final" for e in sink.events))
            final = next(e for e in sink.events if e[0] == "rt_final")
            assert final[1]["discarded"] is True
            errors = [e for e in sink.events if e[0] == "rt_error"]
            assert errors and errors[0][1]["level"] == "info"
            assert app.messages == []
        finally:
            await engine.stop("c1")


class TestIngestWiring:
    async def test_segment_synthesis_and_vector_backfill(
            self, app, ingest_spy, monkeypatch) -> None:
        """流式 ASR 无分段时合成单段入库；分段缺向量时用轮级声纹回填。"""
        from agent.audio import service as audio_service_mod

        async def _embed(self, path: str):
            return [0.1, 0.2, 0.3]

        async def _match(store, vector, channel=""):
            return []

        monkeypatch.setattr(audio_service_mod.AudioService, "speaker_embed", _embed)
        monkeypatch.setattr("agent.audio.matcher.match_vector", _match)

        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            await _speak_one_turn(engine)
            await _wait_for(lambda: len(app.messages) == 1)
            await _wait_for(lambda: len(ingest_spy) == 1)
            payload = ingest_spy[0]
            assert len(payload.segments) == 1
            seg = payload.segments[0]
            assert seg.text == "你好世界"
            assert seg.end_ms > 0
            assert seg.vector == [0.1, 0.2, 0.3]
        finally:
            await engine.stop("c1")

    async def test_recording_retention_wires_ingest(
            self, app, ingest_spy, monkeypatch, tmp_path) -> None:
        """留存开启：轮次 PCM 落盘 WAV，ingest 携带 recording_path 且录制单元登记。"""
        from core.config import ConfigManager
        ConfigManager.set("realtime_keep_recordings", True)
        monkeypatch.setattr("core.path.workspace_root", lambda: str(tmp_path))

        store_calls: list = []

        class _StubStore:
            async def mark_recording(self, path: str, **kwargs) -> None:
                store_calls.append(("mark", path, kwargs))

            async def set_recording_files(self, path: str, files) -> None:
                store_calls.append(("files", path, files))

        monkeypatch.setattr(
            "agent.audio.get_audio_store", lambda: _StubStore(), raising=False)

        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            await _speak_one_turn(engine)
            await _wait_for(lambda: len(ingest_spy) == 1)
            await _wait_for(lambda: any(c[0] == "files" for c in store_calls))
            payload = ingest_spy[0]
            assert payload.recording_path.endswith(".wav")
            assert payload.source_file == payload.recording_path
            import os
            assert os.path.exists(payload.recording_path)
            # 录制单元清单：单文件即本 WAV，回听定位契约
            files_call = next(c for c in store_calls if c[0] == "files")
            assert files_call[2][0]["path"] == payload.recording_path
            assert files_call[2][0]["duration_s"] > 0
        finally:
            ConfigManager.set("realtime_keep_recordings", False)
            await engine.stop("c1")
