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

# 已知流式响应不返回缓存统计的供应商（按模型名前缀登记；其缓存仍在服务端生效，
# 仅流式 usage 缺字段导致不可观测——展示为"不可观测"而非谎报 0%）
_STREAM_CACHE_UNOBSERVABLE: tuple[str, ...] = ("deepseek",)


def stream_cache_unobservable(model: str) -> bool:
    """该模型的流式 usage 是否缺失缓存统计字段（登记式，非特判分支）。"""
    return model.lower().startswith(_STREAM_CACHE_UNOBSERVABLE)


class CacheUsageTracker:
    """最近 LLM 调用缓存用量的环形缓冲（全局单例）。

    按用途分桶（kind）：主对话（reply）与辅助调用（reflect 评审/心跳分析等）
    分开统计——辅助调用无共享前缀，命中率为 0 属正常，混入主口径会误报。
    """

    def __init__(self) -> None:
        self._records: Deque[Dict[str, Any]] = deque(maxlen=_MAX_RECORDS)
        # 未返回 usage 的调用次数（区分"供应商不返 usage"与"返了但无缓存数据"）
        self.no_usage_count: int = 0

    def record(self, usage: UsageInfo, *, kind: str = "reply", model: str = "") -> None:
        """记录一次调用的缓存用量。

        对已知流式缺缓存字段的供应商标记 unobservable，前端显示"不可观测"
        而非误导性的 0%（缓存实际仍在服务端生效，仅无法度量）。
        """
        has_cache_field = (
            usage.cache_read_input_tokens > 0
            or usage.cache_creation_input_tokens > 0
        )
        unobservable = bool(model) and stream_cache_unobservable(model) and not has_cache_field
        self._records.append({
            "ts": time.time(),
            "kind": kind,
            "model": model,
            "unobservable": unobservable,
            "prompt_tokens": usage.prompt_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_hit_rate": round(usage.cache_hit_rate, 4),
        })

    def record_missing(self, *, kind: str = "reply") -> None:
        """记录一次未返回 usage 的调用（流式端点/网关可能丢弃 usage）。"""
        self.no_usage_count += 1

    def last(self, *, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """最近一次调用的缓存用量（可按用途过滤；快照展示用）。"""
        if kind is None:
            return self._records[-1] if self._records else None
        for record in reversed(self._records):
            if record["kind"] == kind:
                return record
        return None

    def summary(self, window: int = 20, *, kind: Optional[str] = None) -> Dict[str, Any]:
        """最近 window 次调用的聚合统计（kind 指定时只统计该用途）。

        unobservable（流式缺缓存字段）的记录不计入命中率均值——
        它们无法度量，混入会把真实命中率拉向 0；单独计数展示。
        """
        records = list(self._records)
        if kind is not None:
            records = [r for r in records if r["kind"] == kind]
        records = records[-window:]
        if not records:
            return {
                "sample_count": 0,
                "avg_cache_hit_rate": 0.0,
                "total_prompt_tokens": 0,
                "total_cache_read_tokens": 0,
                "total_cache_creation_tokens": 0,
                "unobservable_count": 0,
                "no_usage_count": self.no_usage_count,
            }
        unobservable_count = sum(1 for r in records if r.get("unobservable"))
        measurable = [r for r in records if not r.get("unobservable")]
        total_prompt = sum(r["prompt_tokens"] for r in measurable)
        total_read = sum(r["cache_read_input_tokens"] for r in measurable)
        total_creation = sum(r["cache_creation_input_tokens"] for r in measurable)
        return {
            "sample_count": len(records),
            "avg_cache_hit_rate": round(total_read / total_prompt, 4) if total_prompt else 0.0,
            "total_prompt_tokens": total_prompt,
            "total_cache_read_tokens": total_read,
            "total_cache_creation_tokens": total_creation,
            "unobservable_count": unobservable_count,
            "no_usage_count": self.no_usage_count,
        }


# 全局单例
cache_usage_tracker = CacheUsageTracker()
