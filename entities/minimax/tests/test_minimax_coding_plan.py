"""entities/minimax 模块单元测试：凭据解析、请求封装、归一化、图片处理、错误归因。"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock

import httpx
import pytest

import entities.minimax.client as client_mod
from entities.minimax.client import MiniMaxClient, MiniMaxError

_CONFIG = {
    "api_key": "platform-key",
    "coding_plan_api_key": "cp-key",
    "coding_plan_api_host": "",
}


@pytest.fixture(autouse=True)
def _config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(client_mod, "_config_cache", dict(_CONFIG))
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_HOST", raising=False)


class TestCredentialResolution:
    def test_coding_plan_key_priority(self):
        assert MiniMaxClient._coding_plan_key() == "cp-key"

    def test_fallback_to_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(client_mod, "_config_cache", {"api_key": "platform-key"})
        assert MiniMaxClient._coding_plan_key() == "platform-key"

    def test_fallback_to_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(client_mod, "_config_cache", {})
        monkeypatch.setenv("MINIMAX_API_KEY", "env-key")
        assert MiniMaxClient._coding_plan_key() == "env-key"

    def test_host_default(self):
        assert MiniMaxClient._coding_plan_host() == "https://api.minimaxi.com"

    def test_host_config_and_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            client_mod, "_config_cache",
            {"coding_plan_api_host": "https://api.minimax.io"},
        )
        assert MiniMaxClient._coding_plan_host() == "https://api.minimax.io"
        monkeypatch.setattr(client_mod, "_config_cache", {})
        monkeypatch.setenv("MINIMAX_API_HOST", "https://api.minimax.io")
        assert MiniMaxClient._coding_plan_host() == "https://api.minimax.io"

    def test_configured_property(self, monkeypatch: pytest.MonkeyPatch):
        assert MiniMaxClient().coding_plan_configured is True
        monkeypatch.setattr(client_mod, "_config_cache", {})
        assert MiniMaxClient().coding_plan_configured is False


class TestClientMethods:
    async def test_search_payload(self, monkeypatch: pytest.MonkeyPatch):
        mock_post = AsyncMock(return_value={"organic": [], "base_resp": {"status_code": 0}})
        monkeypatch.setattr(MiniMaxClient, "_post_json", mock_post)
        await MiniMaxClient().coding_plan_search("test query")
        mock_post.assert_awaited_once()
        args, kwargs = mock_post.await_args
        assert args[0] == "/v1/coding_plan/search"
        assert args[1] == {"q": "test query"}
        assert kwargs["base_url"] == "https://api.minimaxi.com"
        assert kwargs["headers"]["Authorization"] == "Bearer cp-key"

    async def test_understand_image_returns_content(self, monkeypatch: pytest.MonkeyPatch):
        mock_post = AsyncMock(return_value={"content": "图中是一只猫", "base_resp": {"status_code": 0}})
        monkeypatch.setattr(MiniMaxClient, "_post_json", mock_post)
        result = await MiniMaxClient().coding_plan_understand_image("描述图片", "data:image/png;base64,xx")
        assert result == "图中是一只猫"
        args, _ = mock_post.await_args
        assert args[0] == "/v1/coding_plan/vlm"
        assert args[1]["image_url"] == "data:image/png;base64,xx"

    async def test_understand_image_empty_content(self, monkeypatch: pytest.MonkeyPatch):
        mock_post = AsyncMock(return_value={"content": "", "base_resp": {"status_code": 0}})
        monkeypatch.setattr(MiniMaxClient, "_post_json", mock_post)
        with pytest.raises(MiniMaxError):
            await MiniMaxClient().coding_plan_understand_image("描述图片", "data:image/png;base64,xx")


class TestImageToDataUrl:
    async def test_data_url_passthrough(self):
        assert await client_mod.image_to_data_url("data:image/png;base64,QUJD") == "data:image/png;base64,QUJD"

    async def test_local_file_with_at_prefix(self, tmp_path):
        pic = tmp_path / "pic.png"
        raw = b"\x89PNG"
        pic.write_bytes(raw)
        result = await client_mod.image_to_data_url(f"@{pic}")
        assert result == "data:image/png;base64," + base64.b64encode(raw).decode()

    async def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            await client_mod.image_to_data_url("/nonexistent/x.png")

    async def test_invalid_extension(self, tmp_path):
        pic = tmp_path / "anim.gif"
        pic.write_bytes(b"GIF89a")
        with pytest.raises(ValueError, match="JPEG/PNG/WebP"):
            await client_mod.image_to_data_url(str(pic))

    async def test_http_url_download(self, monkeypatch: pytest.MonkeyPatch):
        class _Resp:
            content = b"\xff\xd8\xff"
            headers = {"content-type": "image/jpeg"}

            def raise_for_status(self) -> None:
                pass

        class _Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *args) -> bool:
                return False

            async def get(self, url: str, follow_redirects: bool = False) -> _Resp:
                return _Resp()

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        result = await client_mod.image_to_data_url("https://example.com/a.jpg")
        assert result.startswith("data:image/jpeg;base64,")

    async def test_http_url_bad_content_type(self, monkeypatch: pytest.MonkeyPatch):
        class _Resp:
            content = b"<html></html>"
            headers = {"content-type": "text/html"}

            def raise_for_status(self) -> None:
                pass

        class _Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self) -> "_Client":
                return self

            async def __aexit__(self, *args) -> bool:
                return False

            async def get(self, url: str, follow_redirects: bool = False) -> _Resp:
                return _Resp()

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        with pytest.raises(ValueError, match="JPEG/PNG/WebP"):
            await client_mod.image_to_data_url("https://example.com/page")


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://api.minimaxi.com/v1/coding_plan/search")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(code, request=req))


class TestErrorResponse:
    def test_http_401_mapped_to_config(self):
        out = json.loads(client_mod.minimax_error_response(_http_status_error(401), "测试"))
        assert out.get("cause") == "config"
        assert out.get("retryable") is False
        assert "hint" in out

    def test_http_429_retryable(self):
        out = json.loads(client_mod.minimax_error_response(_http_status_error(429), "测试"))
        assert out.get("cause") == "network"
        assert out.get("retryable") is True

    def test_http_500_retryable(self):
        out = json.loads(client_mod.minimax_error_response(_http_status_error(503), "测试"))
        assert out.get("cause") == "network"
        assert out.get("retryable") is True

    def test_minimax_error_1004(self):
        out = json.loads(client_mod.minimax_error_response(MiniMaxError(1004, "invalid api key"), "测试"))
        assert out.get("cause") == "config"
        assert "hint" in out

    def test_trace_id_in_message(self):
        e = MiniMaxError(1004, "invalid api key", trace_id="tid-123")
        assert "tid-123" in str(e)
        assert MiniMaxError(1004, "invalid api key").trace_id == ""


class TestNormalizeSearchResults:
    def test_direct(self):
        out = client_mod.normalize_search_results(
            {"organic": [{"title": "t", "link": "https://a.com", "snippet": "s"}]},
            "q", 10,
        )
        assert out["sources"] == 1
        assert out["references"][0]["url"] == "https://a.com"

    def test_empty_hint(self):
        out = client_mod.normalize_search_results({"organic": []}, "q", 10)
        assert out["sources"] == 0
        assert "hint" in out
