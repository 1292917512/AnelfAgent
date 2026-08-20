"""限流 Retry-After 头解析（agent.llm.retry.parse_retry_after）单元测试。"""

from __future__ import annotations

import time

import pytest


class _FakeRateLimitError(Exception):
    """带 headers 属性的限流异常（对齐 litellm RateLimitError 实测形状）。"""

    def __init__(self, headers: dict | None = None) -> None:
        super().__init__("Rate limit exceeded")
        self.headers = headers


def _parse_ok(headers: dict) -> float | None:
    from agent.llm.retry import parse_retry_after

    return parse_retry_after(_FakeRateLimitError(headers))


def test_seconds_value() -> None:
    assert _parse_ok({"Retry-After": "30"}) == 30.0


def test_lowercase_key() -> None:
    assert _parse_ok({"retry-after": "7"}) == 7.0


def test_http_date_value() -> None:
    from email.utils import formatdate

    future = formatdate(time.time() + 45, usegmt=True)
    seconds = _parse_ok({"Retry-After": future})
    assert seconds is not None and 40 <= seconds <= 50


def test_millisecond_variant() -> None:
    assert _parse_ok({"Retry-After-Ms": "2500"}) == 2.5


def test_no_headers_returns_none() -> None:
    from agent.llm.retry import parse_retry_after

    assert parse_retry_after(_FakeRateLimitError(None)) is None
    assert parse_retry_after(Exception("plain")) is None


def test_garbage_and_negative_return_none() -> None:
    from agent.llm.retry import parse_retry_after

    assert parse_retry_after(_FakeRateLimitError({"Retry-After": "soon"})) is None
    assert parse_retry_after(_FakeRateLimitError({"Retry-After": ""})) is None
    assert parse_retry_after(_FakeRateLimitError({"Retry-After": "-5"})) is None


def test_httpx_headers_case_insensitive() -> None:
    httpx = pytest.importorskip("httpx")
    from agent.llm.retry import parse_retry_after

    err = _FakeRateLimitError(httpx.Headers({"retry-after": "12"}))
    assert parse_retry_after(err) == 12.0
