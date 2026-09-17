"""音频上下文注入测试：说话人名单 + 声纹-实体关联可见性。"""

from __future__ import annotations

from agent.audio import matcher
from agent.audio.context import AudioStatusProvider
from agent.audio.store import get_audio_store


def vec(dim: int) -> list[float]:
    return [1.0 if i == dim else 0.0 for i in range(192)]


class TestAudioStatusProvider:
    async def test_bindings_visible_in_injection(self) -> None:
        store = get_audio_store()
        speaker = await matcher.enroll(store, "张三", vec(0), role="家人")
        await store.bind_entity(int(speaker["id"]), "user:webui:u1")
        snap = await AudioStatusProvider().provide("user_webui:u1")
        assert snap is not None
        assert "张三(家人)" in snap.content
        assert "张三(家人)→user:webui:u1" in snap.content
        assert "实体画像" in snap.content
