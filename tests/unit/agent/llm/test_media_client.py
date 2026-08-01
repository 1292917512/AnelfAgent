"""MediaClient HTTP 错误提取（_check_resp）单元测试。"""

from __future__ import annotations

import httpx
import pytest

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
