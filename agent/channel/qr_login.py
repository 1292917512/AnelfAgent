"""扫码登录会话存储 — 各频道 QR 登录管理器共用的会话生命周期原语。

会话对象约定：具备 created_at（与 clock 同源的时间戳）、status、error 属性；
二维码拉取与状态轮询等协议逻辑留在各频道管理器。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Set

# 状态机词汇（与前端扫码组件的轮询契约一致）
QR_WAIT = "wait"
QR_SCANED = "scaned"
QR_CONFIRMED = "confirmed"
QR_TIMEOUT = "timeout"
QR_ERROR = "error"

QR_TERMINAL_STATUSES = frozenset({QR_CONFIRMED, QR_TIMEOUT, QR_ERROR})


class QrSessionStore:
    """扫码会话的登记 / 查询 / 过期回收（TTL 判过期）。"""

    def __init__(self, ttl_seconds: float, *, clock: Callable[[], float] = time.time) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._sessions: Dict[str, Any] = {}

    def add(self, session_id: str, session: Any) -> None:
        self._sessions[session_id] = session

    def get(self, session_id: str) -> Optional[Any]:
        return self._sessions.get(session_id)

    def discard(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def is_expired(self, session: Any) -> bool:
        return self._clock() - session.created_at > self._ttl

    def gc(self, *, keep_status: Set[str] = frozenset()) -> None:
        """回收过期会话；keep_status 中的状态（如 confirmed）即使过期也保留。"""
        dead = [
            sid for sid, s in self._sessions.items()
            if self.is_expired(s) and s.status not in keep_status
        ]
        for sid in dead:
            self._sessions.pop(sid, None)


def qr_result(session: Any, credential: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """组装轮询响应：状态 + 错误信息 + 可选凭据。"""
    out: Dict[str, Any] = {"status": session.status}
    if session.error:
        out["error"] = session.error
    if credential:
        out["credential"] = credential
    return out
