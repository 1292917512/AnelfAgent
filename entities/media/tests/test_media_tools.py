"""entities/media 统一工具面单元测试：recognize_image 的视觉主模型直注与识别链回退。"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

import entities.media.tools as media_tools
from entities.media.tools import recognize_image


@pytest.fixture
def local_image(tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
    """伪造 workspace 内存在的图片文件，沙箱解析直通。"""
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG-fake")
    monkeypatch.setattr(media_tools.utils, "resolve_workspace_path", lambda p: str(img))
    return str(img)


@pytest.fixture
def chain_spy(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """mock 识别链并记录是否被调用。"""
    state: Dict[str, Any] = {"called": False}

    async def _fake(capability: str, label: str, **kwargs: Any) -> Dict[str, Any]:
        state["called"] = True
        return {"success": True, "description": "一只猫", "model": "v1"}

    monkeypatch.setattr(media_tools, "run_capability", _fake)
    return state


class TestMainModelVisionProbe:
    """能力探针的真实路径（不经 monkeypatch 替身）。"""

    def test_runtime_uninitialized_returns_false(self) -> None:
        """运行时未初始化（get_active_llm_client 返回 None）→ False，走识别链。"""
        assert media_tools._main_model_supports_vision() is False


class TestDirectInjectFastPath:
    """主模型具备视觉能力：跳过识别链，直接按 _multimodal 约定回注原图。"""

    @pytest.fixture(autouse=True)
    def _vision_main_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(media_tools, "_main_model_supports_vision", lambda: True)

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
            self, monkeypatch: pytest.MonkeyPatch, chain_spy: Dict[str, Any],
    ) -> None:
        """远程 URL 无法注入本地图片 block，仍走识别链。"""
        out = json.loads(await recognize_image(image_path="https://example.com/a.jpg"))

        assert chain_spy["called"] is True
        assert "_multimodal" not in out

    async def test_video_still_uses_chain(
            self, monkeypatch: pytest.MonkeyPatch, tmp_path, chain_spy: Dict[str, Any],
    ) -> None:
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"fake")
        monkeypatch.setattr(
            media_tools.utils, "resolve_workspace_path", lambda p: str(clip),
        )
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
    """主模型无视觉能力：走媒体库视觉模型链，返回文字描述。"""

    @pytest.fixture(autouse=True)
    def _non_vision_main_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(media_tools, "_main_model_supports_vision", lambda: False)

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
        async def _fail(capability: str, label: str, **kwargs: Any) -> Dict[str, Any]:
            return {"success": False, "error": "无可用视觉模型"}

        monkeypatch.setattr(media_tools, "run_capability", _fail)
        out = json.loads(await recognize_image(image_path="workspace/shot.png"))

        assert "_multimodal" not in out
        assert out["error"] == "无可用视觉模型"
