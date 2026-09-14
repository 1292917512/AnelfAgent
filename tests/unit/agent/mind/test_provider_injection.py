"""上下文提供者实时注入（think_loop 尾部）单元测试。

布局约定：provider 快照每轮收集并置于工具链之后、exec_context 之前——
实时内容（时间/天气等）逐轮新鲜，其字节变化不打断工具链前缀缓存。
"""

from __future__ import annotations

import pytest
from helpers.think_loop_fakes import (
    FakeMind,
    FakePfc,
    end_reply_result,
    run_think_loop,
    tool_result,
)

from agent.mind.tools.think_loop import ThinkMode
from core.context_provider import (
    ContextProviderRegistry,
    ProviderMeta,
    ProviderSnapshot,
)


@pytest.fixture(autouse=True)
def clean_registry():
    ContextProviderRegistry.reset()
    yield
    ContextProviderRegistry.reset()


def _register_counter_provider(counter: list) -> None:
    async def _provide(scope: str) -> ProviderSnapshot:
        counter[0] += 1
        return ProviderSnapshot(content=f"[实时] 第{counter[0]}次收集")

    ContextProviderRegistry.register(
        ProviderMeta(name="fresh_demo", provide_fn=_provide),
    )


def _layer_of(msg: dict) -> str:
    return str(msg.get("_layer", ""))


class TestProviderTailInjection:
    async def test_after_tool_chain_before_exec_context(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """两轮回复：provider 消息位于工具链之后、exec_context 之前，且逐轮新鲜。"""
        # 新鲜度阈值归零：每轮强制重收（验证逐轮新鲜语义）
        monkeypatch.setattr(
            "core.context_provider._COLLECT_FRESH_SECONDS", 0.0,
        )
        counter = [0]
        _register_counter_provider(counter)

        mind = FakeMind(
            rounds=[tool_result("", ["recall"]), end_reply_result()],
            default_text=None,
            pfc=FakePfc(exec_layer=True),
        )
        base = [{"role": "system", "content": "BASE", "_layer": "stable"}]
        await run_think_loop(
            mind, mode=ThinkMode.REPLY, base_messages=base, tools=[{"name": "recall"}],
        )

        assert len(mind.sent_messages) == 2
        round2 = mind.sent_messages[1]

        provider_idx = next(
            i for i, m in enumerate(round2) if _layer_of(m) == "provider"
        )
        exec_idx = next(
            i for i, m in enumerate(round2) if _layer_of(m) == "exec_context"
        )
        # 工具链消息无 _layer 标签（recall 的 assistant/tool 回合）
        chain_idx = max(
            i for i, m in enumerate(round2)
            if not m.get("_layer") and m.get("role") != "system"
            or m.get("role") == "tool"
        )
        assert chain_idx < provider_idx < exec_idx

        # 逐轮新鲜：两轮收集内容不同（计数递增）
        r1_provider = next(
            m for m in mind.sent_messages[0] if _layer_of(m) == "provider"
        )
        r2_provider = round2[provider_idx]
        assert r1_provider["content"] == "[实时] 第1次收集"
        assert r2_provider["content"] == "[实时] 第2次收集"

    async def test_no_providers_no_injection(self) -> None:
        """无注册 provider 时消息结构不受影响（零开销零残留）。"""
        mind = FakeMind(
            rounds=[end_reply_result()], default_text=None,
            pfc=FakePfc(exec_layer=True),
        )
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        round1 = mind.sent_messages[0]
        assert all(_layer_of(m) != "provider" for m in round1)


class TestProviderMediaDispatch:
    """clip 媒体按 kind 分派：image 视觉直注 / 降级标签；audio/video 一律标签引用。"""

    def _register_media_provider(self, media: list, text: str = "[环境] 桌面状态") -> None:
        async def _provide(scope: str) -> ProviderSnapshot:
            return ProviderSnapshot(content=text, media=media)

        ContextProviderRegistry.register(
            ProviderMeta(name="media_demo", provide_fn=_provide),
        )

    def _vision_mind(self) -> FakeMind:
        from types import SimpleNamespace
        mind = FakeMind(
            rounds=[end_reply_result()], default_text=None,
            pfc=FakePfc(exec_layer=True),
        )
        mind.llm = SimpleNamespace(
            config=SimpleNamespace(supports_vision=True, use_flat_image_url=False),
        )
        return mind

    async def test_image_direct_injected_for_vision_model(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """视觉模型：图片组 user 角色多模态消息（image block + 说明文本）。"""
        import base64

        from agent.llm.types import ImageContent

        async def _fake_report(images, *a, **kw):
            return [
                ImageContent(
                    data=base64.b64encode(b"x").decode(), mime_type="image/jpeg",
                ),
            ], [], []

        monkeypatch.setattr("agent.llm.image_utils.ensure_base64_report", _fake_report)
        from core.context_provider import ContextMedia
        self._register_media_provider([ContextMedia.image("/tmp/shot.png")])

        mind = self._vision_mind()
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        msgs = mind.sent_messages[0]

        sys_msg = next(m for m in msgs if _layer_of(m) == "provider" and m["role"] == "system")
        assert sys_msg["content"] == "[环境] 桌面状态"
        media_msg = next(
            m for m in msgs if _layer_of(m) == "provider" and m["role"] == "user"
        )
        assert any(b["type"] == "image_url" for b in media_msg["content"])
        # 图片直注后 clip 文本不再附带媒体标签（避免双重表达）
        assert "[media_type:" not in sys_msg["content"]

    async def test_image_degrades_to_tag_without_vision(self) -> None:
        """非视觉模型：图片降级为媒体标签并入 clip 文本（AI 走媒体工具）。"""
        from core.context_provider import ContextMedia
        self._register_media_provider([ContextMedia.image("/tmp/shot.png")])

        mind = FakeMind(
            rounds=[end_reply_result()], default_text=None,
            pfc=FakePfc(exec_layer=True),
        )  # 无 llm 配置 = 非视觉
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        msgs = mind.sent_messages[0]

        sys_msg = next(m for m in msgs if _layer_of(m) == "provider")
        assert "[media_type:image][media_path:/tmp/shot.png]" in sys_msg["content"]
        assert all(m["role"] != "user" for m in msgs if _layer_of(m) == "provider")

    async def test_audio_video_always_tag_reference(self) -> None:
        """audio/video 即使视觉模型也走标签引用（对话协议层不接受这两类 block）。"""
        from core.context_provider import ContextMedia
        self._register_media_provider([
            ContextMedia.audio("/tmp/env.wav"),
            ContextMedia.video("/tmp/clip.mp4"),
        ])

        mind = self._vision_mind()
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        msgs = mind.sent_messages[0]

        sys_msg = next(m for m in msgs if _layer_of(m) == "provider" and m["role"] == "system")
        assert "[media_type:audio][media_path:/tmp/env.wav]" in sys_msg["content"]
        assert "[media_type:video][media_path:/tmp/clip.mp4]" in sys_msg["content"]
        assert all(m["role"] != "user" for m in msgs if _layer_of(m) == "provider")

    async def test_media_only_clip_creates_reference_message(self) -> None:
        """纯媒体 clip（无文本）也产出标签消息，不静默丢失。"""
        from core.context_provider import ContextMedia
        self._register_media_provider([ContextMedia.audio("/tmp/env.wav")], text="")

        mind = FakeMind(
            rounds=[end_reply_result()], default_text=None,
            pfc=FakePfc(exec_layer=True),
        )
        await run_think_loop(mind, mode=ThinkMode.REPLY, base_messages=[])
        msgs = mind.sent_messages[0]

        sys_msg = next(m for m in msgs if _layer_of(m) == "provider")
        assert sys_msg["content"].startswith("[环境媒体]")
        assert "[media_type:audio]" in sys_msg["content"]
