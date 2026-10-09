"""LLM 调用阶段计时；只记录本进程可观测的边界。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Iterator


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class LLMAttemptTiming:
    """一次供应商/协议尝试的阶段边界。"""

    number: int
    protocol: str
    streaming: bool
    started_mono: float = field(default_factory=monotonic)
    started_at_utc: str = field(default_factory=_utc_now)
    sdk_started_mono: float | None = None
    raw_chunk_mono: float | None = None
    model_delta_mono: float | None = None
    finished_mono: float | None = None
    error: str | None = None
    provider_request_id: str | None = None

    def elapsed_ms(self, point: float | None) -> float | None:
        if point is None:
            return None
        return round((point - self.started_mono) * 1000, 1)

    def snapshot(self) -> dict[str, Any]:
        finished = self.finished_mono or monotonic()
        return {
            "attempt": self.number,
            "protocol": self.protocol,
            "streaming": self.streaming,
            "started_at_utc": self.started_at_utc,
            "duration_ms": round((finished - self.started_mono) * 1000, 1),
            "sdk_start_ms": self.elapsed_ms(self.sdk_started_mono),
            "raw_first_chunk_ms": self.elapsed_ms(self.raw_chunk_mono),
            "model_first_delta_ms": self.elapsed_ms(self.model_delta_mono),
            # LiteLLM 没有暴露实际 socket send 边界，这里明确留空。
            "request_sent_at_utc": None,
            "request_send_observed": False,
            "provider_request_id": self.provider_request_id,
            "error": self.error,
        }


@dataclass(slots=True)
class LLMCallTiming:
    """一次统一 LLM 调用及其所有尝试的可观测计时。"""

    request: dict[str, str]
    purpose: str
    streaming: bool
    started_mono: float = field(default_factory=monotonic)
    started_at_utc: str = field(default_factory=_utc_now)
    finished_mono: float | None = None
    error: str | None = None
    attempts: list[LLMAttemptTiming] = field(default_factory=list)
    _current: LLMAttemptTiming | None = field(default=None, repr=False)

    def begin_attempt(self, protocol: str, streaming: bool) -> LLMAttemptTiming:
        """开始一次真实客户端尝试。"""
        attempt = LLMAttemptTiming(len(self.attempts) + 1, protocol, streaming)
        self.attempts.append(attempt)
        self._current = attempt
        return attempt

    def mark_sdk_started(self) -> None:
        if self._current is not None and self._current.sdk_started_mono is None:
            self._current.sdk_started_mono = monotonic()

    def mark_sdk_finished(self, error: str | None = None) -> None:
        if self._current is None:
            return
        self._current.finished_mono = monotonic()
        self._current.error = error

    def mark_raw_chunk(self) -> None:
        if self._current is not None and self._current.raw_chunk_mono is None:
            self._current.raw_chunk_mono = monotonic()

    def mark_model_delta(self) -> None:
        if self._current is not None and self._current.model_delta_mono is None:
            self._current.model_delta_mono = monotonic()

    def set_provider_request_id(self, value: object) -> None:
        if self._current is None or not isinstance(value, str) or not value:
            return
        self._current.provider_request_id = value

    def finish(self, error: str | None = None) -> None:
        self.finished_mono = monotonic()
        self.error = error

    def snapshot(self) -> dict[str, Any]:
        finished = self.finished_mono or monotonic()
        return {
            "started_at_utc": self.started_at_utc,
            "duration_ms": round((finished - self.started_mono) * 1000, 1),
            "request": dict(self.request),
            "purpose": self.purpose,
            "streaming": self.streaming,
            "error": self.error,
            "attempts": [attempt.snapshot() for attempt in self.attempts],
        }


_CURRENT: ContextVar[LLMCallTiming | None] = ContextVar("llm_call_timing", default=None)


def current_llm_timing() -> LLMCallTiming | None:
    """返回当前调用计时；没有统一调用上下文时为 None。"""
    return _CURRENT.get()


def set_llm_timing(timing: LLMCallTiming) -> Token[LLMCallTiming | None]:
    """绑定当前异步调用上下文并返回可恢复令牌。"""
    return _CURRENT.set(timing)


def reset_llm_timing(token: Token[LLMCallTiming | None]) -> None:
    """恢复绑定前的调用上下文。"""
    _CURRENT.reset(token)


@contextmanager
def llm_timing_context(timing: LLMCallTiming) -> Iterator[Token[LLMCallTiming | None]]:
    """把计时绑定到当前异步调用上下文。"""
    token = set_llm_timing(timing)
    try:
        yield token
    finally:
        reset_llm_timing(token)
