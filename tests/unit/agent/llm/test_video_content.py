"""视频理解链路单元测试。

覆盖 VideoContent 两种协议 block 转换、视频路径识别/加载，以及
LLMClient.describe_video 的 anthropic 直连与 openai video_url 分流。
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict

import pytest

from agent.llm.config import API_TYPE_ANTHROPIC, API_TYPE_OPENAI, LLMClientConfig
from agent.llm.image_utils import is_video_path, load_video_from_path
from agent.llm.llm_client import LLMClient
from agent.llm.types import VideoContent


class TestVideoContentBlock:
    def test_anthropic_block_base64(self) -> None:
        vid = VideoContent(data="QUJD", mime_type="video/mp4")
        assert vid.to_anthropic_block() == {
            "type": "video",
            "source": {"type": "base64", "media_type": "video/mp4", "data": "QUJD"},
        }

    def test_anthropic_block_url(self) -> None:
        vid = VideoContent(data="https://example.com/a.mp4", is_url=True)
        assert vid.to_anthropic_block() == {
            "type": "video",
            "source": {"type": "url", "url": "https://example.com/a.mp4"},
        }

    def test_openai_block_base64(self) -> None:
        vid = VideoContent(data="QUJD", mime_type="video/webm")
        assert vid.to_openai_block() == {
            "type": "video_url",
            "video_url": {"url": "data:video/webm;base64,QUJD"},
        }

    def test_openai_block_url(self) -> None:
        vid = VideoContent(data="https://example.com/a.mp4", is_url=True)
        assert vid.to_openai_block() == {
            "type": "video_url",
            "video_url": {"url": "https://example.com/a.mp4"},
        }


class TestIsVideoPath:
    @pytest.mark.parametrize("path", [
        "workspace/uploads/video/a.mp4",
        "/tmp/b.MOV",
        "https://example.com/c.webm?token=xyz",
        "d.mkv#frag",
    ])
    def test_video_paths(self, path: str) -> None:
        assert is_video_path(path)

    @pytest.mark.parametrize("path", [
        "workspace/uploads/image/a.jpg",
        "https://example.com/b.png?x=1",
        "no_extension",
        "doc.pdf",
    ])
    def test_non_video_paths(self, path: str) -> None:
        assert not is_video_path(path)


class TestLoadVideoFromPath:
    def test_loads_base64_with_mime(self, tmp_path) -> None:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00\x01\x02")
        vid = load_video_from_path(str(f))
        assert vid.mime_type == "video/mp4"
        assert not vid.is_url
        assert base64.b64decode(vid.data) == b"\x00\x01\x02"

    def test_unknown_video_ext_defaults_mp4(self, tmp_path) -> None:
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"x")
        vid = load_video_from_path(str(f))
        assert vid.mime_type.startswith("video/")

    def test_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            load_video_from_path(str(tmp_path / "none.mp4"))


def _make_config(api_type: str) -> LLMClientConfig:
    return LLMClientConfig(
        name="test",
        model="qwen3.8-max",
        base_url="https://example.com/apps/anthropic",
        api_key="sk-test",
        api_type=api_type,
        supports_vision=True,
    )


class _FakeResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any], text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self) -> Dict[str, Any]:
        return self._payload


class _FakeHttp:
    def __init__(self, resp: _FakeResponse) -> None:
        self._resp = resp
        self.last_url = ""
        self.last_payload: Dict[str, Any] = {}
        self.last_headers: Dict[str, str] = {}

    async def post(self, url: str, json: Dict[str, Any], headers: Dict[str, str]) -> _FakeResponse:
        self.last_url = url
        self.last_payload = json
        self.last_headers = headers
        return self._resp


class TestDescribeVideo:
    @pytest.mark.asyncio
    async def test_anthropic_direct_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """anthropic 端点绕过 litellm 直连 /v1/messages，发原生 video block。"""
        client = LLMClient(_make_config(API_TYPE_ANTHROPIC))
        resp = _FakeResponse(200, {"content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "一只猫在玩耍。"},
        ]})
        fake = _FakeHttp(resp)
        monkeypatch.setattr(client, "_direct_http", lambda: fake)

        vid = VideoContent(data="QUJD", mime_type="video/mp4")
        text = await client.describe_video(vid, prompt="描述视频")

        assert text == "一只猫在玩耍。"
        assert fake.last_url == "https://example.com/apps/anthropic/v1/messages"
        assert fake.last_headers["x-api-key"] == "sk-test"
        assert fake.last_headers["anthropic-version"] == "2023-06-01"
        content = fake.last_payload["messages"][0]["content"]
        assert content[0] == vid.to_anthropic_block()
        assert content[1] == {"type": "text", "text": "描述视频"}
        assert fake.last_payload["model"] == "qwen3.8-max"

    @pytest.mark.asyncio
    async def test_anthropic_error_raises_with_detail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LLMClient(_make_config(API_TYPE_ANTHROPIC))
        resp = _FakeResponse(400, {"code": "InvalidParameter", "message": "bad video"})
        fake = _FakeHttp(resp)
        monkeypatch.setattr(client, "_direct_http", lambda: fake)

        with pytest.raises(RuntimeError, match="HTTP 400"):
            await client.describe_video(VideoContent(data="QUJD"))

    @pytest.mark.asyncio
    async def test_anthropic_empty_text_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = LLMClient(_make_config(API_TYPE_ANTHROPIC))
        resp = _FakeResponse(200, {"content": [{"type": "thinking", "thinking": "..."}]})
        fake = _FakeHttp(resp)
        monkeypatch.setattr(client, "_direct_http", lambda: fake)

        with pytest.raises(RuntimeError, match="空结果"):
            await client.describe_video(VideoContent(data="QUJD"))

    @pytest.mark.asyncio
    async def test_openai_goes_through_chat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """非 anthropic 端点走 chat() 的 video_url block。"""
        client = LLMClient(_make_config(API_TYPE_OPENAI))
        captured: Dict[str, Any] = {}

        class _Result:
            content = "视频描述"

        async def _fake_chat(messages: list[dict], options: Any = None) -> Any:
            captured["messages"] = messages
            return _Result()

        monkeypatch.setattr(client, "chat", _fake_chat)
        vid = VideoContent(data="QUJD", mime_type="video/mp4")
        text = await client.describe_video(vid, prompt="描述视频")

        assert text == "视频描述"
        content = captured["messages"][0]["content"]
        assert {"type": "text", "text": "描述视频"} in content
        assert vid.to_openai_block() in content
