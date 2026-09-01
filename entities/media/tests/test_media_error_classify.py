"""媒体能力错误归因（entities.media.providers.base.classify_media_errors）单元测试。"""

from __future__ import annotations

from entities._sdk import ErrorCause
from entities.media.providers.base import classify_media_errors as _classify_media_errors


class TestClassifyMediaErrors:
    def test_auth(self) -> None:
        cause, retryable, hint = _classify_media_errors(
            {"m1": "HTTP 401: missing api secret key (1004)"}
        )
        assert cause == ErrorCause.CONFIG
        assert not retryable
        assert "密钥" in hint

    def test_balance(self) -> None:
        cause, retryable, hint = _classify_media_errors(
            {"m1": "MiniMax API 错误 (1008): 余额不足"}
        )
        assert cause == ErrorCause.CONFIG
        assert not retryable
        assert "余额" in hint

    def test_sensitive_content(self) -> None:
        cause, retryable, hint = _classify_media_errors(
            {"m1": "HTTP 422: sensitive content (1026)"}
        )
        assert cause == ErrorCause.PARAM
        assert not retryable
        assert "敏感" in hint

    def test_timeout(self) -> None:
        cause, retryable, _ = _classify_media_errors(
            {"m1": "视频生成超时（600s 内未完成）"}
        )
        assert cause == ErrorCause.TIMEOUT
        assert retryable

    def test_default_network(self) -> None:
        cause, retryable, hint = _classify_media_errors(
            {"m1": "HTTP 500: internal error (1000)"}
        )
        assert cause == ErrorCause.NETWORK
        assert retryable
        assert hint

    def test_expired_link(self) -> None:
        cause, retryable, hint = _classify_media_errors({
            "models": "无法下载图片（链接可能已过期）: https://cdn.example.com/img?token=...",
            "minimax": "图片下载失败（HTTP 400），链接已过期或失效",
        })
        assert cause == ErrorCause.NOT_FOUND
        assert not retryable
        assert "过期" in hint
