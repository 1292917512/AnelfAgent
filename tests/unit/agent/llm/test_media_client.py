"""MediaClient HTTP 错误提取（_check_resp）与 DashScope 图片协议单元测试。"""

from __future__ import annotations

import httpx
import pytest

from agent.llm.image_adapters import DashScopeImagesAdapter
from agent.llm.media_client import MediaClient


def _resp(status_code: int, body: object = None, text: str = "") -> httpx.Response:
    if body is not None:
        return httpx.Response(status_code, json=body)
    return httpx.Response(status_code, text=text)


class TestCheckResp:
    def test_success_noop(self) -> None:
        MediaClient._check_resp(_resp(200, {"ok": True}))
        MediaClient._check_resp(_resp(302))

    def test_minimax_v2_error_envelope(self) -> None:
        body = {"type": "error", "error": {"type": "authorized_error", "message": "missing api secret key (1004)"}}
        with pytest.raises(RuntimeError) as exc_info:
            MediaClient._check_resp(_resp(401, body))
        msg = str(exc_info.value)
        assert "HTTP 401" in msg
        assert "missing api secret key (1004)" in msg

    def test_minimax_v1_base_resp(self) -> None:
        body = {"base_resp": {"status_code": 1008, "status_msg": "余额不足"}}
        with pytest.raises(RuntimeError) as exc_info:
            MediaClient._check_resp(_resp(402, body))
        assert "HTTP 402" in str(exc_info.value)
        assert "[1008] 余额不足" in str(exc_info.value)

    def test_openai_style_error(self) -> None:
        body = {"error": {"message": "model not found", "type": "invalid_request_error"}}
        with pytest.raises(RuntimeError, match="model not found"):
            MediaClient._check_resp(_resp(400, body))

    def test_plain_message_field(self) -> None:
        with pytest.raises(RuntimeError, match="rate limited"):
            MediaClient._check_resp(_resp(429, {"message": "rate limited"}))

    def test_non_json_body_falls_back_to_text(self) -> None:
        with pytest.raises(RuntimeError, match="Bad Gateway"):
            MediaClient._check_resp(_resp(502, text="Bad Gateway"))


class TestDashScopeImageEdit:
    def test_edit_request_messages_with_image(self) -> None:
        adapter = DashScopeImagesAdapter()
        req = adapter.build_edit_request(
            "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            model="qwen-image-3.0-pro",
            prompt="把猫变成戴眼镜的样子",
            image_content="https://oss.example/cat.png",
            num_inference_steps=20,
            cfg=4.0,
        )
        assert req.url == (
            "https://token-plan.cn-beijing.maas.aliyuncs.com"
            "/api/v1/services/aigc/multimodal-generation/generation"
        )
        payload = req.payload or {}
        content = payload["input"]["messages"][0]["content"]
        assert content == [
            {"image": "https://oss.example/cat.png"},
            {"text": "把猫变成戴眼镜的样子"},
        ]
        assert payload["parameters"] == {"n": 1}

    def test_edit_response_reuses_extract_urls(self) -> None:
        adapter = DashScopeImagesAdapter()
        # qwen-image 系列内容项只有 image 键（实测），wan 系列带 type 字段，两种都要解析
        qwen_style = {"output": {"choices": [{"message": {"content": [
            {"image": "https://oss.example/edited.png"},
        ]}}]}}
        assert adapter.extract_urls(qwen_style) == ["https://oss.example/edited.png"]
        wan_style = {"output": {"choices": [{"message": {"content": [
            {"type": "image", "image": "https://oss.example/edited2.png"},
        ]}}]}}
        assert adapter.extract_urls(wan_style) == ["https://oss.example/edited2.png"]
