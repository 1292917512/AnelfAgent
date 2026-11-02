"""频道消息去重 — TTL + 容量双约束的内存去重器（各频道消息入口共用）。"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Optional


class MessageDedup:
    """键去重器：TTL 过期 + 容量淘汰（先清过期，仍超限淘汰最旧一半）。

    ttl_seconds 为 None 时只做容量淘汰（无过期语义）。
    """

    def __init__(self, ttl_seconds: Optional[float] = 300, max_size: int = 2000) -> None:
        if max_size < 1:
            raise ValueError("去重器容量必须为正数")
        self._ttl_seconds = ttl_seconds
        self._max_size = max_size
        self._seen: OrderedDict[str, float] = OrderedDict()

    def is_duplicate(self, key: str) -> bool:
        """键已存在（未过期）返回 True；否则登记并返回 False。"""
        now = time.monotonic()
        timestamp = self._seen.get(key)
        if timestamp is not None and (self._ttl_seconds is None or now - timestamp <= self._ttl_seconds):
            return True
        self._evict(now)
        self._seen[key] = now
        return False

    def discard(self, key: str) -> None:
        """撤回尚未成功受理的消息，允许调用方修正或重试。"""
        self._seen.pop(key, None)

    def _evict(self, now: float) -> None:
        if self._ttl_seconds is not None:
            while self._seen:
                first = next(iter(self._seen))
                if now - self._seen[first] <= self._ttl_seconds:
                    break
                del self._seen[first]
        if len(self._seen) >= self._max_size:
            for _ in range(max(1, len(self._seen) // 2)):
                self._seen.popitem(last=False)
