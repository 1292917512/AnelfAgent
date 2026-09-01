"""自适应重试工具（参考 hermes-agent retry_utils）。

提供带抖动的指数退避，避免多实例同时重试造成的惊群效应，
以及限流响应 Retry-After 头的解析。
"""
from __future__ import annotations

import random
from typing import Optional

# 服务端 Retry-After 的采信上限（秒）：超过视为本轮放弃当前候选转回退链，
# 而非干等——服务端要求等 10 分钟时，本地重试注定撞墙，白烧一次请求与配额
RETRY_AFTER_WAIT_CAP = 60.0

# 解析的头部候选（覆盖秒数/毫秒与常见大小写；httpx.Headers 本身大小写不敏感）
_RETRY_HEADER_NAMES = ("Retry-After", "retry-after", "Retry-After-Ms", "retry-after-ms")


def jittered_backoff(
        attempt: int,
        *,
        base_delay: float = 2.0,
        max_delay: float = 60.0,
        jitter_ratio: float = 0.5,
) -> float:
    """计算带抖动的指数退避等待时间（秒）。

    Args:
        attempt: 第几次重试（从 1 开始）
        base_delay: 基础等待时间
        max_delay: 等待时间上限
        jitter_ratio: 抖动比例（0~1），在 [0, ratio * delay] 区间随机加码
    """
    attempt = max(1, attempt)
    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
    # 抖动加码后仍钳制在上限内（capped delay 上加码会超出文档宣称的上限 50%）
    return min(delay + random.uniform(0, jitter_ratio * delay), max_delay)


def parse_retry_after(exception: BaseException) -> Optional[float]:
    """从异常携带的响应头解析 Retry-After，返回建议等待秒数。

    litellm 的 RateLimitError 等异常实例携带 ``headers`` 属性（本机已验证）。
    支持两种标准取值：秒数（``Retry-After: 30``）与 HTTP 日期
    （``Retry-After: Wed, 21 Oct 2026 07:28:00 GMT``），另兼容毫秒变体
    ``Retry-After-Ms``。无法解析（无头/垃圾值/负值）返回 None，调用方
    回退本地指数退避。
    """
    headers = getattr(exception, "headers", None)
    if not headers:
        return None
    for name in _RETRY_HEADER_NAMES:
        try:
            value = headers.get(name)
        except Exception:
            value = None
        if value is None:
            continue
        seconds = _parse_retry_after_value(value)
        if seconds is None:
            continue
        if name.lower().endswith("-ms"):
            seconds /= 1000.0
        return seconds if seconds >= 0 else None
    return None


def _parse_retry_after_value(value: object) -> Optional[float]:
    """解析单个 Retry-After 取值：秒数或 HTTP 日期 → 距现在的秒数。"""
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    import time
    from email.utils import parsedate_to_datetime
    try:
        target = parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None
    return target - time.time()
