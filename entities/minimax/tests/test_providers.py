"""MiniMax 能力组件（entities/minimax/providers.py）单元测试。

组件注册与能力分发：视觉组件（understand/image_gen）、声音组件
（tts/voice_mgmt）、检索组件（search），客户端以替身注入。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest

import entities.minimax.providers as providers_mod
from entities.minimax.providers import (
    MiniMaxSoundProvider,
    MiniMaxVisualProvider,
)


class _FakeClient:
    """可控 MiniMax 客户端替身。"""

    def __init__(self, configured: bool = True, coding_plan: bool = False) -> None:
        self.configured = configured
        self.coding_plan_configured = coding_plan
        self.calls: list = []

    async def text_to_speech(self, text: str, **kwargs: Any) -> bytes:
        self.calls.append(("tts", text, kwargs))
        return b"audio-bytes"

    async def generate_image(self, prompt: str, **kwargs: Any) -> list:
        self.calls.append(("image_gen", prompt, kwargs))
        return ["https://cdn.example.com/a.png"]

    async def coding_plan_understand_image(self, prompt: str, data_url: str) -> str:
        self.calls.append(("understand", prompt, data_url))
        return "一只猫"


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch):
    state: Dict[str, Any] = {"client": _FakeClient()}
    monkeypatch.setattr(providers_mod, "_client", lambda: state["client"])
    return state


class TestVisualProvider:
    async def test_image_guide_is_lazy_unique_and_does_not_change_prompt_or_tools(
        self, monkeypatch: pytest.MonkeyPatch, fake_client: Dict[str, Any],
    ) -> None:
        from agent.capabilities import CapabilityRouter
        from agent.context.assembly import ContextAssembly
        from agent.vision import capabilities, gen_tools
        from core.entity import EntityRegistry
        from entities.minimax import guide

        fake_client["client"] = _FakeClient(configured=False, coding_plan=False)
        router = CapabilityRouter(config_key="test_image_guide", default_chain=[], log_tag="test",
                                  allow_fallback=lambda capability: False)
        monkeypatch.setattr(capabilities, "_router", router)
        assembly = ContextAssembly(SimpleNamespace(), SimpleNamespace())
        prompt_before = assembly.build_tool_system_prompt()
        catalog_before = EntityRegistry.get_entity_catalog()
        schemas_before = EntityRegistry.get_tool_schemas()
        assert EntityRegistry.get("minimax_image_guide") is None
        assert not any(item["group"] == "minimax" for item in catalog_before)
        calls: list[bool] = []
        original = guide.image_setup_guide

        def load_guide() -> dict[str, Any]:
            calls.append(True)
            return original()

        monkeypatch.setattr(guide, "image_setup_guide", load_guide)
        router.register(MiniMaxVisualProvider())
        summary = json.loads(await gen_tools.vision_config("capabilities"))
        assert summary["capabilities"]["image_gen"]["available"] is False
        assert not calls
        status = json.loads(await gen_tools.vision_config("providers"))
        details = status["providers"][0]["details"]
        assert details["image_gen"]["setup_guide"]["setup"][1]["arguments"]["timeout"] == 650
        assert details["image_edit"] == {"setup_guide_capability": "image_gen"}
        assert calls == [True]
        assert assembly.build_tool_system_prompt() == prompt_before
        assert EntityRegistry.get_entity_catalog() == catalog_before
        assert EntityRegistry.get_tool_schemas() == schemas_before
        router.unregister("minimax")
        assert json.loads(await gen_tools.vision_config("providers"))["providers"] == []
        assert assembly.build_tool_system_prompt() == prompt_before

    def test_is_configured_split_credential(self, fake_client):
        provider = MiniMaxVisualProvider()
        fake_client["client"] = _FakeClient(configured=True, coding_plan=False)
        assert provider.is_configured("image_gen") is True
        assert provider.is_configured("understand") is False

    async def test_understand_uses_coding_plan(self, fake_client, monkeypatch: pytest.MonkeyPatch):
        fake_client["client"] = _FakeClient(coding_plan=True)

        async def _data_url(src: str) -> str:
            return "data:image/png;base64,xx"

        monkeypatch.setattr(
            "entities.minimax.client.image_to_data_url", _data_url,
        )
        out = await MiniMaxVisualProvider().run(
            "understand", image_path="https://a.com/x.png", prompt="描述",
        )
        assert out["description"] == "一只猫"
        assert out["model"] == "minimax-coding-plan"

    async def test_understand_rejects_video(self, fake_client):
        from entities._sdk import CapabilityNotSupported
        with pytest.raises(CapabilityNotSupported):
            await MiniMaxVisualProvider().run(
                "understand", image_path="/tmp/a.mp4", prompt="描述",
            )

    async def test_image_gen_preserves_size_for_protocol_adapter(self, fake_client):
        out = await MiniMaxVisualProvider().run(
            "image_gen", prompt="猫", image_size="1664x928",
            num_inference_steps=20, n=1, reference_image="",
        )
        assert out["image_results"] == ["https://cdn.example.com/a.png"]
        assert fake_client["client"].calls[0][2]["image_size"] == "1664x928"

    async def test_unsupported_capability_rejected(self, fake_client):
        from entities._sdk import CapabilityNotSupported
        with pytest.raises(CapabilityNotSupported):
            await MiniMaxVisualProvider().run("video", op="generate")


class TestSoundProvider:
    async def test_tts_call(self, fake_client):
        out = await MiniMaxSoundProvider().run(
            "tts", text="你好", voice="v1", references=None,
            emotion="happy", speed=1.2, pitch=0, language_boost="",
        )
        assert out["audio_bytes"] == b"audio-bytes"
        kind, text, kwargs = fake_client["client"].calls[0]
        assert kind == "tts"
        assert kwargs["voice_id"] == "v1"
        assert kwargs["emotion"] == "happy"

    async def test_tts_references_unsupported(self, fake_client):
        from entities._sdk import CapabilityNotSupported
        with pytest.raises(CapabilityNotSupported):
            await MiniMaxSoundProvider().run(
                "tts", text="你好", voice="",
                references=[{"audio": "x", "text": "y"}],
                emotion="", speed=0.0, pitch=0, language_boost="",
            )

    async def test_unconfigured_raises_unavailable(self, fake_client):
        from entities._sdk import ProviderUnavailable
        fake_client["client"] = _FakeClient(configured=False)
        with pytest.raises(ProviderUnavailable):
            await MiniMaxSoundProvider().run(
                "tts", text="你好", voice="", references=None,
                emotion="", speed=0.0, pitch=0, language_boost="",
            )


class TestComponentRegistration:
    def test_entity_import_registers_all_components(self):
        """实体包导入即完成四类组件注册（视觉/声音/检索/流式 TTS）。"""
        import entities.minimax

        # 同名覆盖幂等：重复注册不重复占位（测试间路由可能被重置）
        entities.minimax.register_components()

        from agent.audio.capabilities import get_sound_router
        from agent.retrieval import providers as retrieval_providers
        from agent.tts import get_tts_registry
        from agent.vision.capabilities import get_visual_router

        assert get_visual_router().get("minimax") is not None
        assert get_sound_router().get("minimax") is not None
        assert retrieval_providers.get_provider("minimax").name == "minimax"
        tts_names = [p.name for p in get_tts_registry().list()]
        assert "minimax" in tts_names
        assert "minimax_ws" in tts_names
