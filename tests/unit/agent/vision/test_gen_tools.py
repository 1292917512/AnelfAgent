"""视觉生成工具（agent.vision.gen_tools）单元测试：主模型直注与识别链回退。"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

import agent.vision.gen_tools as gen_tools
from agent.vision.gen_tools import recognize_image, recognize_video


@pytest.fixture
def local_image(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """伪造 workspace 内存在的图片文件，沙箱解析直通。"""
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    monkeypatch.setattr(gen_tools.ws, "resolve_workspace_path", lambda p: str(img))
    return str(img)


@pytest.fixture
def local_video(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """伪造 workspace 内存在的视频文件，沙箱解析直通。"""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake")
    monkeypatch.setattr(gen_tools.ws, "resolve_workspace_path", lambda p: str(clip))
    return str(clip)


@pytest.fixture
def chain_spy(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """mock 视觉能力路由并记录调用情况。"""
    state: Dict[str, Any] = {"called": False}

    class _FakeRouter:
        def names(self) -> list:
            return ["models"]

        async def run(self, capability: str, label: str, **kwargs: Any) -> Dict[str, Any]:
            state["called"] = True
            state["capability"] = capability
            state["label"] = label
            state["kwargs"] = kwargs
            return {"success": True, "description": "一只猫", "model": "v1"}

    monkeypatch.setattr(gen_tools, "get_visual_router", lambda: _FakeRouter())
    return state


class TestMainModelVisionProbe:
    """能力探针的真实路径（不经 monkeypatch 替身）。"""

    def test_runtime_uninitialized_returns_false(self) -> None:
        """运行时未初始化（get_runtime 返回 None）→ False，走识别链。"""
        assert gen_tools._main_model_supports_vision() is False


class TestDirectInjectFastPath:
    """主模型具备视觉能力：跳过识别链，直接按 _multimodal 约定回注原图。"""

    @pytest.fixture(autouse=True)
    def _vision_main_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gen_tools, "_main_model_supports_vision", lambda: True)

    async def test_skips_chain_and_injects(
            self, local_image: str, chain_spy: Dict[str, Any],
    ) -> None:
        out = json.loads(await recognize_image(
            image_path="workspace/shot.png", prompt="评估渲染质量",
        ))

        assert chain_spy["called"] is False
        assert out["success"] is True
        assert out["_multimodal"] is True
        assert out["images"] == [local_image]
        assert out["image_path"] == local_image
        # 分析要求随注入文本自包含（模型轮内可见自己的调用参数，此为兜底）
        assert "评估渲染质量" in out["text"]

    async def test_url_still_uses_chain(
            self, chain_spy: Dict[str, Any],
    ) -> None:
        """远程 URL 无法注入本地图片 block，仍走识别链。"""
        out = json.loads(await recognize_image(image_path="https://example.com/a.jpg"))

        assert chain_spy["called"] is True
        assert "_multimodal" not in out

    async def test_video_still_uses_chain(
            self, local_video: str, chain_spy: Dict[str, Any],
    ) -> None:
        out = json.loads(await recognize_image(image_path="workspace/clip.mp4"))

        assert chain_spy["called"] is True
        assert "_multimodal" not in out

    async def test_explicit_provider_forces_chain(
            self, local_image: str, chain_spy: Dict[str, Any],
    ) -> None:
        """显式指定 provider = 明确要求识别链，不走直注。"""
        out = json.loads(await recognize_image(
            image_path="workspace/shot.png", provider="models",
        ))

        assert chain_spy["called"] is True
        assert "_multimodal" not in out
        assert out["description"] == "一只猫"


class TestVisionChainFallback:
    """主模型无视觉能力：走视觉模型链，返回文字描述。"""

    @pytest.fixture(autouse=True)
    def _non_vision_main_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gen_tools, "_main_model_supports_vision", lambda: False)

    async def test_chain_description_returned(
            self, local_image: str, chain_spy: Dict[str, Any],
    ) -> None:
        out = json.loads(await recognize_image(image_path="workspace/shot.png"))

        assert chain_spy["called"] is True
        assert out["description"] == "一只猫"
        assert out["model"] == "v1"
        assert out["image_path"] == local_image
        assert "_multimodal" not in out

    async def test_chain_failure_not_injected(
            self, monkeypatch: pytest.MonkeyPatch, local_image: str,
    ) -> None:
        class _FailRouter:
            def names(self) -> list:
                return ["models"]

            async def run(self, capability: str, label: str, **kwargs: Any) -> Dict[str, Any]:
                return {"success": False, "error": "无可用视觉模型"}

        monkeypatch.setattr(gen_tools, "get_visual_router", lambda: _FailRouter())
        out = json.loads(await recognize_image(image_path="workspace/shot.png"))

        assert "_multimodal" not in out
        assert out["error"] == "无可用视觉模型"


class TestRecognizeVideo:
    """recognize_video 独立工具入口：视频始终走识别链，不直注主模型。"""

    async def test_video_uses_chain_even_with_vision_main_model(
            self, monkeypatch: pytest.MonkeyPatch, local_video: str, chain_spy: Dict[str, Any],
    ) -> None:
        """主模型有视觉也不直注（视频无法注入本地 block），走视频识别链。"""
        monkeypatch.setattr(gen_tools, "_main_model_supports_vision", lambda: True)
        out = json.loads(await recognize_video(video_path="workspace/clip.mp4"))

        assert chain_spy["called"] is True
        assert chain_spy["capability"] == "understand"
        assert chain_spy["label"] == "视频识别"
        assert chain_spy["kwargs"]["image_path"] == local_video
        assert out["description"] == "一只猫"
        assert out["image_path"] == local_video
        assert "_multimodal" not in out

    async def test_default_prompt_is_video(
            self, local_video: str, chain_spy: Dict[str, Any],
    ) -> None:
        await recognize_video(video_path="workspace/clip.mp4")

        assert "视频" in chain_spy["kwargs"]["prompt"]

    async def test_missing_path_param_error(self) -> None:
        out = json.loads(await recognize_video())
        assert "error" in out
