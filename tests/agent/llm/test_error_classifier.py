"""LLM 错误分类器与退避工具（agent.llm.error_classifier / retry）单元测试。"""

from __future__ import annotations

import asyncio

import litellm
import pytest

from agent.llm.error_classifier import ErrorCategory, classify_llm_error
from agent.llm.retry import jittered_backoff


class TestClassifyByExceptionType:
    def test_timeout(self) -> None:
        c = classify_llm_error(asyncio.TimeoutError("timeout"))
        assert c.category == ErrorCategory.TIMEOUT
        assert c.retryable

    def test_rate_limit(self) -> None:
        exc = litellm.RateLimitError(
            "rate limited", model="m", llm_provider="openai",
        )
        c = classify_llm_error(exc)
        assert c.category == ErrorCategory.RATE_LIMIT
        assert c.retryable

    def test_context_window(self) -> None:
        exc = litellm.ContextWindowExceededError(
            "too long", model="m", llm_provider="openai",
        )
        c = classify_llm_error(exc)
        assert c.category == ErrorCategory.CONTEXT_OVERFLOW
        assert not c.retryable
        assert c.should_compress

    def test_auth(self) -> None:
        exc = litellm.AuthenticationError(
            "bad key", model="m", llm_provider="openai",
        )
        c = classify_llm_error(exc)
        assert c.category == ErrorCategory.AUTH
        assert not c.retryable

    def test_bad_request(self) -> None:
        exc = litellm.BadRequestError(
            "bad param", model="m", llm_provider="openai",
        )
        c = classify_llm_error(exc)
        assert c.category == ErrorCategory.PARAM_ERROR
        assert not c.retryable

    def test_not_found(self) -> None:
        exc = litellm.NotFoundError("no model", model="m", llm_provider="openai")
        c = classify_llm_error(exc)
        assert c.category == ErrorCategory.NOT_FOUND
        assert c.should_fallback

    def test_service_unavailable(self) -> None:
        exc = litellm.ServiceUnavailableError(
            "down", model="m", llm_provider="openai",
        )
        c = classify_llm_error(exc)
        assert c.category == ErrorCategory.OVERLOADED
        assert c.retryable
        assert c.should_fallback


class TestClassifyByMessagePattern:
    def test_wrapped_context_error(self) -> None:
        c = classify_llm_error(RuntimeError("Error: prompt is too long, max context 128k"))
        assert c.category == ErrorCategory.CONTEXT_OVERFLOW
        assert c.should_compress

    def test_wrapped_rate_limit(self) -> None:
        c = classify_llm_error(RuntimeError("429 too many requests"))
        assert c.category == ErrorCategory.RATE_LIMIT

    def test_unknown(self) -> None:
        c = classify_llm_error(RuntimeError("weird unexpected thing"))
        assert c.category == ErrorCategory.UNKNOWN
        assert c.retryable


class TestJitteredBackoff:
    def test_monotonic_base(self) -> None:
        d1 = jittered_backoff(1, base_delay=2.0, jitter_ratio=0.0)
        d2 = jittered_backoff(2, base_delay=2.0, jitter_ratio=0.0)
        d3 = jittered_backoff(3, base_delay=2.0, jitter_ratio=0.0)
        assert d1 == 2.0 and d2 == 4.0 and d3 == 8.0

    def test_max_delay_cap(self) -> None:
        d = jittered_backoff(20, base_delay=2.0, max_delay=60.0, jitter_ratio=0.0)
        assert d == 60.0

    def test_jitter_within_bounds(self) -> None:
        for _ in range(50):
            d = jittered_backoff(1, base_delay=2.0, max_delay=60.0, jitter_ratio=0.5)
            assert 2.0 <= d <= 3.0

    def test_attempt_floor(self) -> None:
        d = jittered_backoff(0, base_delay=2.0, jitter_ratio=0.0)
        assert d == 2.0
