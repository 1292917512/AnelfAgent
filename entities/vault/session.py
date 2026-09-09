"""VaultSession：解锁会话——DEK 内存驻留 + 可选闲置自动锁定。

主密码派生/机器密钥解裹的 DEK 仅在内存持有，进程重启必然回到锁定态。
- 主密码模式：带 TTL（``vault_auto_lock_minutes``），每次使用滑动顺延，超时自动锁定
- 机器密钥模式：无 TTL（常驻解锁，service 层透明重解锁，AI 零摩擦）
"""

from __future__ import annotations

import asyncio
from typing import Optional

from core.log import log


class VaultLockedError(Exception):
    """密码本处于锁定状态，需要先解锁。"""


class VaultSession:
    """单进程内的密码本解锁会话。"""

    def __init__(self) -> None:
        self._dek: Optional[bytes] = None
        self._timer: Optional[asyncio.TimerHandle] = None
        self._ttl_seconds: Optional[int] = None

    @property
    def is_unlocked(self) -> bool:
        return self._dek is not None

    @property
    def dek(self) -> bytes:
        """取 DEK 并刷新闲置计时（滑动过期；无 TTL 时直通）。"""
        if self._dek is None:
            raise VaultLockedError("密码本已锁定，请先解锁")
        self._touch()
        return self._dek

    def unlock(self, dek: bytes, ttl_seconds: Optional[int] = None) -> None:
        self._dek = dek
        self._ttl_seconds = ttl_seconds
        self._touch()
        log("密码本已解锁", "INFO")

    def lock(self) -> None:
        had = self._dek is not None
        self._dek = None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if had:
            log("密码本已锁定", "INFO")

    def remaining_seconds(self) -> int:
        if self._timer is None or self._dek is None:
            return 0
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return 0
        when = getattr(self._timer, "_when", None)
        if when is None:
            return 0
        return max(0, int(when - loop.time()))

    def _touch(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._ttl_seconds is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._timer = loop.call_later(max(1, self._ttl_seconds), self._on_timeout)

    def _on_timeout(self) -> None:
        self._dek = None
        self._timer = None
        log("密码本闲置超时，已自动锁定", "INFO")
