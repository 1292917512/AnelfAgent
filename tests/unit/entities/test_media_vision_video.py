"""models provider 视觉路由单元测试：视频文件分流到 describe_video 链路。"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from entities.media.providers.models import ModelsProvider


class TestVisionVideoRouting:
    @pytest.mark.asyncio
    async def test_video_path_routes_to_run_video(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = ModelsProvider()
        called: Dict[str, Any] = {}

        async def _fake_run_video(video_path: str, prompt: str) -> Dict[str, Any]:
            called["video_path"] = video_path
            called["prompt"] = prompt
            return {"description": "ok", "model": "m"}

        monkeypatch.setattr(provider, "_run_video", _fake_run_video)
        out = await provider._run_vision("workspace/uploads/video/a.mp4", "描述")
        assert out["description"] == "ok"
        assert called == {"video_path": "workspace/uploads/video/a.mp4", "prompt": "描述"}

    @pytest.mark.asyncio
    async def test_video_url_routes_to_run_video(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = ModelsProvider()

        async def _fake_run_video(video_path: str, prompt: str) -> Dict[str, Any]:
            return {"description": "ok"}

        monkeypatch.setattr(provider, "_run_video", _fake_run_video)
        out = await provider._run_vision("https://example.com/a.webm?x=1", "描述")
        assert out["description"] == "ok"
