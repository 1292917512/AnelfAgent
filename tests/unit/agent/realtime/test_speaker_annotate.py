"""实时语音说话人标注单元测试：只读识别 → 消息前缀标签（应对由 AI 决定）。"""

from __future__ import annotations

import pytest
from rt_fakes import FakeSink, make_delivery
from test_engine import _wait_for, pcm_silence, pcm_tone

from agent.realtime.engine import RealtimeEngine

RATE = 16000

# 共用引擎测试的注册表隔离（注入假流式 ASR/TTS）与 app 替身
pytestmark = pytest.mark.usefixtures("clean_registries")


@pytest.fixture
def no_voiceprint(monkeypatch: pytest.MonkeyPatch):
    """声纹识别不可用（无提供者）：标注静默缺失，不阻塞语音轮。"""

    async def _no_embed(self, path: str):
        return None

    from agent.audio import service as audio_service_mod
    monkeypatch.setattr(audio_service_mod.AudioService, "speaker_embed", _no_embed)


class TestSpeakerAnnotation:
    async def test_matched_speaker_prefixes_message(self, app, monkeypatch) -> None:
        """命中已知说话人：消息前缀 [语音 说话人:名 置信:x]。"""
        from agent.audio import service as audio_service_mod

        async def _embed(self, path: str):
            return [0.1, 0.2, 0.3]

        async def _match(store, vector, channel=""):
            return [{"matched": True, "id": 1, "name": "张三",
                     "speaker_key": "spk_1", "similarity": 0.87,
                     "entity_scope": "user:qq:456"}]

        monkeypatch.setattr(audio_service_mod.AudioService, "speaker_embed", _embed)
        monkeypatch.setattr("agent.audio.matcher.match_vector", _match)

        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: len(app.messages) == 1)
            content = app.messages[0]["content"]
            assert content.startswith("[语音 说话人:张三 置信:0.87]")
            # 绑定实体以机器可解析标签随行（思维层据此自动召回画像/记忆）
            assert "[speaker_scope:user:qq:456]" in content
            assert "你好世界" in content
        finally:
            await engine.stop("c1")

    async def test_unmatched_speaker_marks_unregistered(self, app, monkeypatch) -> None:
        """未命中：标注 未注册（多环境多人对话中即新面孔，识别建档由音频库负责）。"""
        from agent.audio import service as audio_service_mod

        async def _embed(self, path: str):
            return [0.1, 0.2, 0.3]

        async def _match(store, vector, channel=""):
            return [{"matched": False, "id": 2, "name": "临时",
                     "speaker_key": "spk_tmp", "similarity": 0.3}]

        monkeypatch.setattr(audio_service_mod.AudioService, "speaker_embed", _embed)
        monkeypatch.setattr("agent.audio.matcher.match_vector", _match)

        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: len(app.messages) == 1)
            assert app.messages[0]["content"].startswith("[语音 说话人:未注册]")
        finally:
            await engine.stop("c1")

    async def test_no_provider_skips_annotation(self, app, no_voiceprint) -> None:
        """无声纹提供者：消息原样，无标注前缀。"""
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: len(app.messages) == 1)
            assert app.messages[0]["content"] == "你好世界"
        finally:
            await engine.stop("c1")

    async def test_config_gate_disables_annotation(self, app, monkeypatch) -> None:
        """realtime_speaker_annotate=false：即使可识别也不标注。"""
        from agent.audio import service as audio_service_mod
        from core.config import ConfigManager

        real_get = ConfigManager.get
        monkeypatch.setattr(
            ConfigManager, "get",
            staticmethod(lambda k, d=None: (
                False if k == "realtime_speaker_annotate" else real_get(k, d))))

        async def _embed(self, path: str):
            return [0.1]

        monkeypatch.setattr(audio_service_mod.AudioService, "speaker_embed", _embed)
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: len(app.messages) == 1)
            assert app.messages[0]["content"] == "你好世界"
        finally:
            await engine.stop("c1")

    async def test_identify_failure_does_not_block_turn(self, app, monkeypatch) -> None:
        """识别抛异常：静默跳过标注，语音轮照常进思维。"""
        from agent.audio import service as audio_service_mod

        async def _boom(self, path: str):
            raise RuntimeError("voiceprint backend down")

        monkeypatch.setattr(audio_service_mod.AudioService, "speaker_embed", _boom)
        engine = RealtimeEngine()
        sink = FakeSink()
        await engine.start("c1", make_delivery(), sink.as_sink(), RATE)
        try:
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_silence(20))
            for _ in range(50):
                await engine.accept_pcm("c1", pcm_tone(20))
            for _ in range(60):
                await engine.accept_pcm("c1", pcm_silence(20))
            await _wait_for(lambda: len(app.messages) == 1)
            assert app.messages[0]["content"] == "你好世界"
        finally:
            await engine.stop("c1")
