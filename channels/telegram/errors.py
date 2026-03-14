"""Telegram 错误分类与重试策略。"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Awaitable, Callable, Optional, TypeVar

from core.log import log

T = TypeVar("T")


class TelegramSendError(RuntimeError):
    """Telegram 发送失败。"""

    def __init__(self, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.recoverable = recoverable


def is_recoverable_error(exc: BaseException) -> bool:
    """网络超时、连接重置、429 等可恢复错误。"""
    msg = str(exc).lower()
    if is_rate_limited(exc):
        return True
    recoverable_keywords = (
        "timeout", "timed out", "connection reset", "connection refused",
        "temporary failure", "server error", "502", "503", "504",
    )
    return any(k in msg for k in recoverable_keywords)


def is_rate_limited(exc: BaseException) -> bool:
    """检测 Telegram 429 Flood 错误。"""
    try:
        from telegram.error import RetryAfter
        return isinstance(exc, RetryAfter)
    except ImportError:
        pass
    return "429" in str(exc) or "flood" in str(exc).lower()


def get_retry_after(exc: BaseException) -> float:
    """从 429 错误中提取等待秒数。"""
    try:
        from telegram.error import RetryAfter
        if isinstance(exc, RetryAfter):
            return float(exc.retry_after)
    except (ImportError, AttributeError):
        pass
    return 5.0


def is_thread_not_found(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "thread not found" in msg or "message_thread_id" in msg


def is_html_parse_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "can't parse" in msg and ("entities" in msg or "html" in msg)


def is_chat_migrated(exc: BaseException) -> Optional[int]:
    """如果是 ChatMigrated 错误，返回新 chat_id；否则返回 None。"""
    try:
        from telegram.error import ChatMigrated
        if isinstance(exc, ChatMigrated):
            return exc.new_chat_id
    except ImportError:
        pass
    return None


def is_forbidden(exc: BaseException) -> bool:
    try:
        from telegram.error import Forbidden
        return isinstance(exc, Forbidden)
    except ImportError:
        return "forbidden" in str(exc).lower()


async def with_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    max_attempts: int = 3,
    label: str = "telegram",
    **kwargs: Any,
) -> T:
    """带重试和速率限制感知的异步调用。"""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if is_rate_limited(exc):
                wait = get_retry_after(exc)
                log(f"{label}: 速率限制，等待 {wait}s (attempt {attempt}/{max_attempts})", "WARNING")
                await asyncio.sleep(wait)
                continue
            if is_recoverable_error(exc) and attempt < max_attempts:
                delay = min(2 ** attempt, 16)
                log(f"{label}: 可恢复错误，{delay}s 后重试 (attempt {attempt}/{max_attempts}): {exc}", "WARNING")
                await asyncio.sleep(delay)
                continue
            raise
    raise last_exc  # type: ignore[misc]
