"""models provider 视觉识别下载优先：URL 一律先转 base64 再喂模型。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Optional

import pytest

import entities._sdk as sdk
from agent.llm.types import ImageContent
from entities.media.providers import models as models_mod
from entities.media.providers.models import ModelsProvider

URL = "https://cdn.example.com/photo.jpg?token=abc"


class _FakeVisionClient:
    """记录收到的图片形态（URL 直传 / base64）的视觉模型桩。"""

    def __init__(self, name: str) -> None:
        self.config = SimpleNamespace(
            name=name, supports_url_vision=True, supports_base64_vision=True,
        )
        self.seen_is_url: List[bool] = []

    async def describe_images(self, images: list, prompt: str = "") -> str:
        self.seen_is_url.append(bool(images[0].is_url))
        return "desc"


def _patch_mgr(monkeypatch: pytest.MonkeyPatch, clients: list) -> None:
    monkeypatch.setattr(
        models_mod, "_mgr",
        lambda: SimpleNamespace(get_all_by_type=lambda _t: clients),
    )
    monkeypatch.setattr(sdk, "get_model_type_enum", lambda: SimpleNamespace(VISION="vision"))


class TestVisionDownloadFirst:
    @pytest.mark.asyncio
    async def test_url_converted_to_base64_before_models(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """即使模型支持 url 视觉，URL 也先下载转 base64，不直传给端点。"""
        client = _FakeVisionClient("m1")
        _patch_mgr(monkeypatch, [client])

        async def _fake_download(url: str) -> ImageContent:
            return ImageContent(data="aGk=", is_url=False)

        monkeypatch.setattr(sdk, "download_image_to_base64", _fake_download)
        out = await ModelsProvider()._run_vision(URL, "描述")
        assert out["description"] == "desc"
        assert client.seen_is_url == [False]

    @pytest.mark.asyncio
    async def test_download_failure_raises_expired_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """下载失败直接抛'链接可能已过期'，不再逐个模型空转。"""

        async def _fail_download(url: str) -> Optional[ImageContent]:
            return None

        _patch_mgr(monkeypatch, [_FakeVisionClient("m1")])
        monkeypatch.setattr(sdk, "download_image_to_base64", _fail_download)
        with pytest.raises(RuntimeError, match="已过期"):
            await ModelsProvider()._run_vision(URL, "描述")
