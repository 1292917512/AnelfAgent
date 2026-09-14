"""models 视觉链单元测试（内容审核回退 / URL 下载优先 / 视频分流）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List, Optional

import litellm
import pytest

import agent.llm.image_utils as image_utils
import agent.vision.capabilities as caps_mod
from agent.llm.types import ImageContent
from agent.vision.capabilities import ModelsVisualProvider

_LOCAL = "/tmp/fake_photo.png"
_URL = "https://cdn.example.com/photo.jpg?token=abc"


def _sensitive_error() -> litellm.InternalServerError:
    return litellm.InternalServerError(
        'AnthropicException - {"type":"error","error":{"type":"api_error",'
        '"message":"input new_sensitive, messages[0]\'s content[1] image is '
        'sensitive, please check your input (1026)"}}',
        model="m", llm_provider="anthropic",
    )


class _FakeVisionClient:
    """视觉模型桩：可注入异常，记录调用次数与收到的图片形态。"""

    def __init__(self, name: str, error: Exception | None = None) -> None:
        self.config = SimpleNamespace(
            name=name, supports_url_vision=True, supports_base64_vision=True,
        )
        self._error = error
        self.calls = 0
        self.seen_is_url: List[bool] = []

    async def describe_images(self, images: list, prompt: str = "") -> str:
        self.calls += 1
        self.seen_is_url.append(bool(images[0].is_url))
        if self._error is not None:
            raise self._error
        return f"desc-by-{self.config.name}"


def _patch_mgr(monkeypatch: pytest.MonkeyPatch, clients: list) -> None:
    """装配最小模型管理器桩。"""
    monkeypatch.setattr(
        caps_mod, "_mgr",
        lambda: SimpleNamespace(get_all_by_type=lambda _t: clients),
    )


def _patch_common(monkeypatch: pytest.MonkeyPatch, clients: list) -> List[int]:
    """装配桩：模型管理器 + 本地加载 + 计数版优化。"""
    _patch_mgr(monkeypatch, clients)
    monkeypatch.setattr(image_utils, "is_video_path", lambda _p: False)
    monkeypatch.setattr(
        image_utils, "load_image_from_path",
        lambda _p: ImageContent(data="aGk=", is_url=False),
    )
    optimize_calls: List[int] = []
    monkeypatch.setattr(
        image_utils, "optimize_for_vision",
        lambda img: (optimize_calls.append(1), img)[1],
    )
    return optimize_calls


class TestVisionContentPolicy:
    @pytest.mark.asyncio
    async def test_content_policy_falls_back_to_next_provider(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """审核拒绝是同模型确定性拒绝，但不同供应商尺度不同，回退须保留。"""
        mini = _FakeVisionClient("MiniMax-M3", error=_sensitive_error())
        k3 = _FakeVisionClient("k3-1m")
        _patch_common(monkeypatch, [mini, k3])

        out = await ModelsVisualProvider()._run_understand(_LOCAL, "描述")
        assert out["description"] == "desc-by-k3-1m"
        assert out["model"] == "k3-1m"
        assert mini.calls == 1 and k3.calls == 1

    @pytest.mark.asyncio
    async def test_all_content_policy_rejects_raise_clear_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """全链均为审核拒绝时，错误须明确指向内容审核而非笼统'调用失败'。"""
        clients = [
            _FakeVisionClient("m1", error=_sensitive_error()),
            _FakeVisionClient("m2", error=_sensitive_error()),
        ]
        _patch_common(monkeypatch, clients)

        with pytest.raises(RuntimeError, match="内容审核"):
            await ModelsVisualProvider()._run_understand(_LOCAL, "描述")

    @pytest.mark.asyncio
    async def test_mixed_failures_raise_generic_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """审核拒绝与其他故障混合时，保持笼统'调用失败'口径。"""
        clients = [
            _FakeVisionClient("m1", error=_sensitive_error()),
            _FakeVisionClient("m2", error=RuntimeError("connection reset")),
        ]
        _patch_common(monkeypatch, clients)

        with pytest.raises(RuntimeError, match="均调用失败"):
            await ModelsVisualProvider()._run_understand(_LOCAL, "描述")

    @pytest.mark.asyncio
    async def test_optimize_runs_once_outside_candidate_loop(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """图片优化在候选循环外执行一次，不随回退重复压缩。"""
        clients = [
            _FakeVisionClient("m1", error=RuntimeError("boom")),
            _FakeVisionClient("m2", error=RuntimeError("boom")),
        ]
        optimize_calls = _patch_common(monkeypatch, clients)

        with pytest.raises(RuntimeError):
            await ModelsVisualProvider()._run_understand(_LOCAL, "描述")
        assert len(optimize_calls) == 1


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

        monkeypatch.setattr(image_utils, "download_image_to_base64", _fake_download)
        out = await ModelsVisualProvider()._run_understand(_URL, "描述")
        assert out["description"] == "desc-by-m1"
        assert client.seen_is_url == [False]

    @pytest.mark.asyncio
    async def test_download_failure_raises_expired_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """下载失败直接抛'链接可能已过期'，不再逐个模型空转。"""

        async def _fail_download(url: str) -> Optional[ImageContent]:
            return None

        _patch_mgr(monkeypatch, [_FakeVisionClient("m1")])
        monkeypatch.setattr(image_utils, "download_image_to_base64", _fail_download)
        with pytest.raises(RuntimeError, match="已过期"):
            await ModelsVisualProvider()._run_understand(_URL, "描述")


class TestVisionVideoRouting:
    @pytest.mark.asyncio
    async def test_video_path_routes_to_video_understand(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = ModelsVisualProvider()
        called = {}

        async def _fake_run_video(video_path: str, prompt: str) -> dict:
            called["video_path"] = video_path
            called["prompt"] = prompt
            return {"description": "ok", "model": "m"}

        monkeypatch.setattr(provider, "_run_video_understand", _fake_run_video)
        out = await provider._run_understand("workspace/uploads/video/a.mp4", "描述")
        assert out["description"] == "ok"
        assert called == {"video_path": "workspace/uploads/video/a.mp4", "prompt": "描述"}

    @pytest.mark.asyncio
    async def test_video_url_routes_to_video_understand(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = ModelsVisualProvider()

        async def _fake_run_video(video_path: str, prompt: str) -> dict:
            return {"description": "ok"}

        monkeypatch.setattr(provider, "_run_video_understand", _fake_run_video)
        out = await provider._run_understand("https://example.com/a.webm?x=1", "描述")
        assert out["description"] == "ok"


class _FakeVideoClient:
    """可编排 describe_video 行为的假视觉客户端。"""

    def __init__(self, name: str, supports_video: bool, error: Exception | None = None) -> None:
        self.config = SimpleNamespace(name=name, supports_video=supports_video)
        self._error = error
        self.calls = 0

    async def describe_video(self, video: object, prompt: str = "") -> str:
        self.calls += 1
        if self._error:
            raise self._error
        return f"{self.config.name} 的描述"


class TestVideoCandidateFilter:
    """ModelsVisualProvider._run_video_understand：按 supports_video 声明过滤候选模型。"""

    @staticmethod
    def _patch_env(monkeypatch: pytest.MonkeyPatch, clients: list) -> None:
        monkeypatch.setattr(
            caps_mod, "_mgr",
            lambda: SimpleNamespace(get_all_by_type=lambda _mt: clients),
        )
        monkeypatch.setattr(
            image_utils, "load_video_from_path", lambda _p: SimpleNamespace(data="x"),
        )

    @pytest.mark.asyncio
    async def test_prefers_supports_video_declared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """有模型声明 supports_video 时，未声明模型不投送整段视频。"""
        plain = _FakeVideoClient("plain", supports_video=False)
        capable = _FakeVideoClient("capable", supports_video=True)
        self._patch_env(monkeypatch, [plain, capable])

        out = await ModelsVisualProvider()._run_video_understand("/tmp/x.mp4", "描述")

        assert out["model"] == "capable"
        assert plain.calls == 0
        assert capable.calls == 1

    @pytest.mark.asyncio
    async def test_none_declared_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无模型声明 supports_video → 报配置缺失，不做全链喷洒试错。"""
        from agent.capabilities import ProviderUnavailable

        plain = _FakeVideoClient("plain", supports_video=False)
        self._patch_env(monkeypatch, [plain])

        with pytest.raises(ProviderUnavailable, match="supports_video"):
            await ModelsVisualProvider()._run_video_understand("/tmp/x.mp4", "描述")
        assert plain.calls == 0
