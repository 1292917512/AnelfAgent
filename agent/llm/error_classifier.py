"""LLM 错误分类器（参考 hermes-agent error_classifier，按 litellm 异常体系裁剪）。

将 LLM 调用异常分类为可操作的类别，每种类别对应不同的处理策略：
- rate_limit:        指数退避 + 抖动后重试
- overloaded:        固定间隔重试，连续失败切换备用模型
- server_error:      退避重试
- timeout:           退避重试
- context_overflow:  不重试，触发上下文压缩后重试
- auth:              不重试（凭证问题重试无意义）
- param_error:       不重试，直接反馈 AI 修正参数
- not_found:         不重试，切换备用模型
- unknown:           保守重试一次
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ErrorCategory(str, Enum):
    """LLM 错误类别。"""

    RATE_LIMIT = "rate_limit"
    OVERLOADED = "overloaded"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    CONTEXT_OVERFLOW = "context_overflow"
    AUTH = "auth"
    PARAM_ERROR = "param_error"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassifiedError:
    """分类后的错误及处理策略标志。"""

    category: ErrorCategory
    retryable: bool = True
    should_compress: bool = False
    should_fallback: bool = False
    message: str = ""
    status_code: Optional[int] = None


# ------------------------------------------------------------------
# 消息模式表（供应商包装后的错误文本兜底识别）
# ------------------------------------------------------------------

_RATE_LIMIT_PATTERNS = (
    "rate limit", "rate_limit", "too many requests", "429",
    "quota exceeded", "requests per minute", "tokens per minute",
)

_OVERLOADED_PATTERNS = (
    "overloaded", "capacity", "service unavailable", "503",
    "temporarily unavailable", "server is busy",
)

_CONTEXT_OVERFLOW_PATTERNS = (
    "context window", "context length", "context limit",
    "context_window_exceeded", "prompt is too long", "too many tokens",
    "token limit", "max context", "maximum context",
)

_AUTH_PATTERNS = (
    "invalid api key", "incorrect api key", "unauthorized", "401",
    "authentication", "permission denied", "403", "invalid token",
)

_PARAM_ERROR_PATTERNS = (
    "invalid parameter", "invalid request", "bad request", "400",
    "validation error", "malformed", "invalid schema",
)

# 流式响应体内的错误载荷（litellm 以通用异常抛出，无类型与状态码）
_STREAM_ERROR_PATTERNS = (
    "raised a streaming error", "unable to parse response",
)


def _match_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(p in text for p in patterns)


def classify_llm_error(exc: BaseException) -> ClassifiedError:
    """分类 LLM 调用异常，返回处理策略。

    分类优先级：litellm 异常类型 → HTTP 状态码 → 消息模式匹配 → unknown。
    """
    import asyncio

    import litellm

    message = str(exc)
    msg_lower = message.lower()
    status_code = getattr(exc, "status_code", None)

    # ---- 超时 ----
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, litellm.Timeout)):
        return ClassifiedError(
            ErrorCategory.TIMEOUT, retryable=True, message=message,
            status_code=status_code,
        )

    # ---- 上下文超限（不重试，触发压缩）----
    if isinstance(exc, litellm.ContextWindowExceededError) or _match_any(
        msg_lower, _CONTEXT_OVERFLOW_PATTERNS
    ):
        return ClassifiedError(
            ErrorCategory.CONTEXT_OVERFLOW, retryable=False,
            should_compress=True, message=message, status_code=status_code,
        )

    # ---- 限流 ----
    if isinstance(exc, litellm.RateLimitError) or _match_any(msg_lower, _RATE_LIMIT_PATTERNS):
        return ClassifiedError(
            ErrorCategory.RATE_LIMIT, retryable=True, message=message,
            status_code=status_code or 429,
        )

    # ---- 过载 / 服务不可用 ----
    if isinstance(exc, litellm.ServiceUnavailableError) or _match_any(
        msg_lower, _OVERLOADED_PATTERNS
    ):
        return ClassifiedError(
            ErrorCategory.OVERLOADED, retryable=True, should_fallback=True,
            message=message, status_code=status_code or 503,
        )

    # ---- 认证 / 权限（不重试）----
    if isinstance(exc, (litellm.AuthenticationError, litellm.PermissionDeniedError)):
        return ClassifiedError(
            ErrorCategory.AUTH, retryable=False, message=message,
            status_code=status_code or 401,
        )

    # ---- 模型不存在（切换备用模型）----
    if isinstance(exc, litellm.NotFoundError):
        return ClassifiedError(
            ErrorCategory.NOT_FOUND, retryable=False, should_fallback=True,
            message=message, status_code=status_code or 404,
        )

    # ---- 参数错误（不重试，反馈 AI 修正）----
    if isinstance(exc, (litellm.BadRequestError, ValueError)):
        return ClassifiedError(
            ErrorCategory.PARAM_ERROR, retryable=False, message=message,
            status_code=status_code or 400,
        )

    # ---- 服务器错误 ----
    if isinstance(exc, litellm.InternalServerError) or (
        isinstance(status_code, int) and status_code >= 500
    ):
        return ClassifiedError(
            ErrorCategory.SERVER_ERROR, retryable=True, message=message,
            status_code=status_code,
        )

    # ---- 消息模式兜底 ----
    if _match_any(msg_lower, _AUTH_PATTERNS):
        return ClassifiedError(
            ErrorCategory.AUTH, retryable=False, message=message,
            status_code=status_code,
        )
    if _match_any(msg_lower, _PARAM_ERROR_PATTERNS):
        return ClassifiedError(
            ErrorCategory.PARAM_ERROR, retryable=False, message=message,
            status_code=status_code,
        )
    if _match_any(msg_lower, _STREAM_ERROR_PATTERNS):
        return ClassifiedError(
            ErrorCategory.SERVER_ERROR, retryable=True, message=message,
            status_code=status_code,
        )

    # ---- 未知错误：保守重试 ----
    return ClassifiedError(
        ErrorCategory.UNKNOWN, retryable=True, message=message,
        status_code=status_code,
    )
