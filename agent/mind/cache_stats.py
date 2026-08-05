"""供应商侧 prompt 缓存用量追踪器。

记录最近若干次 LLM 调用的真实缓存命中/写入 tokens（内存环形缓冲），
供上下文快照、状态 API 展示真实缓存命中率。

纯内存、零 I/O；usage 解析本身在 response_parsing 完成，此处仅聚合数值。
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque, Dict, Optional

from agent.llm.types import UsageInfo

_MAX_RECORDS = 100


class CacheUsageTracker:
    """最近 LLM 调用缓存用量的环形缓冲（全局单例）。"""

    def __init__(self) -> None:
        self._records: Deque[Dict[str, Any]] = deque(maxlen=_MAX_RECORDS)
        # 未返回 usage 的调用次数（区分"供应商不返 usage"与"返了但无缓存数据"）
        self.no_usage_count: int = 0

    def record(self, usage: UsageInfo) -> None:
        """记录一次调用的缓存用量（无缓存数据的供应商记 0，不影响平均值口径）。"""
        self._records.append({
            "ts": time.time(),
            "prompt_tokens": usage.prompt_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_hit_rate": round(usage.cache_hit_rate, 4),
        })

    def record_missing(self) -> None:
        """记录一次未返回 usage 的调用（流式端点/网关可能丢弃 usage）。"""
        self.no_usage_count += 1

    def last(self) -> Optional[Dict[str, Any]]:
        """最近一次调用的缓存用量（快照附加展示用）。"""
        return self._records[-1] if self._records else None

    def summary(self, window: int = 20) -> Dict[str, Any]:
        """最近 window 次调用的聚合统计。"""
        records = list(self._records)[-window:]
        if not records:
            return {
                "sample_count": 0,
                "avg_cache_hit_rate": 0.0,
                "total_prompt_tokens": 0,
                "total_cache_read_tokens": 0,
                "total_cache_creation_tokens": 0,
                "no_usage_count": self.no_usage_count,
            }
        total_prompt = sum(r["prompt_tokens"] for r in records)
        total_read = sum(r["cache_read_input_tokens"] for r in records)
        total_creation = sum(r["cache_creation_input_tokens"] for r in records)
        return {
            "sample_count": len(records),
            "avg_cache_hit_rate": round(total_read / total_prompt, 4) if total_prompt else 0.0,
            "total_prompt_tokens": total_prompt,
            "total_cache_read_tokens": total_read,
            "total_cache_creation_tokens": total_creation,
            "no_usage_count": self.no_usage_count,
        }


# 全局单例
cache_usage_tracker = CacheUsageTracker()
