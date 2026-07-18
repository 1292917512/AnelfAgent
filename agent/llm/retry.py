"""自适应重试工具（参考 hermes-agent retry_utils）。

提供带抖动的指数退避，避免多实例同时重试造成的惊群效应。
"""
from __future__ import annotations

import itertools
import random
import time

# 全局计数器：为每次退避计算提供去相关的种子
_tick_counter = itertools.count(1)


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
    seed = (time.time_ns() ^ (next(_tick_counter) * 0x9E3779B9)) & 0xFFFFFFFF
    jitter = random.Random(seed).uniform(0, jitter_ratio * delay)
    return delay + jitter
