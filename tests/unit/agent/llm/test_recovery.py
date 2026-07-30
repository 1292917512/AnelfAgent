"""候选链恢复策略（agent.llm.resilience.recovery）单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import litellm

from agent.llm.resilience.recovery import (
    is_overflow_error,
    next_fallback_index,
    should_try_fallback_candidate,
)


def _overflow_exc() -> Exception:
    return litellm.ContextWindowExceededError(
        "prompt is too long", model="m", llm_provider="openai",
    )


def _rate_limit_exc() -> Exception:
    return litellm.RateLimitError(
        "rate limited", model="m", llm_provider="openai",
    )


def _client(name: str, window: int) -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(
        name=name, litellm_model=f"openai/{name}", context_window=window,
    ))


class TestIsOverflowError:
    def test_overflow_detected(self) -> None:
        assert is_overflow_error(_overflow_exc())

    def test_rate_limit_not_overflow(self) -> None:
        assert not is_overflow_error(_rate_limit_exc())


class TestShouldTryFallbackCandidate:
    """溢出场景：只放行窗口更大的候选；其余错误一律放行。"""

    def test_overflow_larger_window_allowed(self) -> None:
        with patch(
            "agent.llm.resilience.recovery._candidate_context_window",
            side_effect=[128_000, 200_000],
        ):
            assert should_try_fallback_candidate(
                _overflow_exc(), _client("a", 128_000), _client("b", 200_000),
            )

    def test_overflow_same_window_skipped(self) -> None:
        with patch(
            "agent.llm.resilience.recovery._candidate_context_window",
            side_effect=[128_000, 128_000],
        ):
            assert not should_try_fallback_candidate(
                _overflow_exc(), _client("a", 128_000), _client("b", 128_000),
            )

    def test_overflow_smaller_window_skipped(self) -> None:
        with patch(
            "agent.llm.resilience.recovery._candidate_context_window",
            side_effect=[200_000, 32_000],
        ):
            assert not should_try_fallback_candidate(
                _overflow_exc(), _client("a", 200_000), _client("b", 32_000),
            )

    def test_unknown_window_conservatively_allowed(self) -> None:
        # 任一窗口未知 → 宁可多试一次
        with patch(
            "agent.llm.resilience.recovery._candidate_context_window",
            side_effect=[0, 128_000],
        ):
            assert should_try_fallback_candidate(
                _overflow_exc(), _client("a", 0), _client("b", 128_000),
            )

    def test_non_overflow_always_allowed(self) -> None:
        # 限流错误不做窗口比较
        assert should_try_fallback_candidate(
            _rate_limit_exc(), _client("a", 200_000), _client("b", 8_000),
        )


class TestNextFallbackIndex:
    def test_skips_to_larger_window(self) -> None:
        candidates = [
            _client("primary", 128_000),
            _client("same", 128_000),
            _client("smaller", 32_000),
            _client("bigger", 200_000),
        ]
        with patch(
            "agent.llm.resilience.recovery._candidate_context_window",
            side_effect=lambda c: c.config.context_window,
        ):
            idx = next_fallback_index(_overflow_exc(), candidates[0], candidates, 1)
        assert idx == 3

    def test_no_worthy_candidate_returns_none(self) -> None:
        candidates = [
            _client("primary", 200_000),
            _client("same", 200_000),
            _client("smaller", 32_000),
        ]
        with patch(
            "agent.llm.resilience.recovery._candidate_context_window",
            side_effect=lambda c: c.config.context_window,
        ):
            idx = next_fallback_index(_overflow_exc(), candidates[0], candidates, 1)
        assert idx is None

    def test_non_overflow_returns_next(self) -> None:
        candidates = [_client("a", 200_000), _client("b", 8_000)]
        assert next_fallback_index(_rate_limit_exc(), candidates[0], candidates, 1) == 1
