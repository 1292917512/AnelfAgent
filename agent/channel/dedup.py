"""频道消息去重 — TTL + 容量双约束的内存去重器（各频道消息入口共用）。"""

from __future__ import annotations

import time
from typing import Dict, Optional


class MessageDedup:
    """键去重器：TTL 过期 + 容量淘汰（先清过期，仍超限淘汰最旧一半）。

    ttl_seconds 为 None 时只做容量淘汰（无过期语义）。
    """

    def __init__(self, ttl_seconds: Optional[float] = 300, max_size: int = 2000) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_size = max_size
        self._seen: Dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        """键已存在（未过期）返回 True；否则登记并返回 False。"""
        now = time.time()
        self._evict(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def _evict(self, now: float) -> None:
        if self._ttl_seconds is not None:
            expired = [k for k, ts in self._seen.items() if now - ts > self._ttl_seconds]
            for k in expired:
                del self._seen[k]
        if len(self._seen) >= self._max_size:
            ordered = sorted(self._seen.items(), key=lambda kv: kv[1])
            for k, _ in ordered[: len(ordered) // 2]:
                del self._seen[k]
