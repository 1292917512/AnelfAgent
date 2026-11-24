"""插件操作状态板 — 进行中操作与最近失败的轻量事实记录。

PluginManager 的变更操作经 track_operation 埋点；上下文提供者
（entities/plugins）每轮读取快照注入 volatile 层——有进行中/失败事项
才产生一行文本，常态为空零占用。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional
from uuid import uuid4

from core.sanitizer import sanitize_text

# 失败记录保留窗口（秒）：超过即视为已处置，不再占用上下文
_FAILURE_TTL = 1800.0
# 失败记录条数上限
_FAILURE_CAP = 5


class OperationBoard:
    """插件操作状态板（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current = ContextVar[str]("plugin_operation", default="")
        self._active: dict[str, dict[str, Any]] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=48)
        self._failures: List[Dict[str, Any]] = []

    def stage(self, stage: str, *, name: str = "") -> None:
        """更新当前调用的阶段，嵌套操作与并发线程各自归属。"""
        with self._lock:
            row = self._active.get(self._current.get())
            if row is not None:
                row["stage"] = stage
                if name:
                    row["name"] = name

    @contextmanager
    def track(self, action: str, name: str) -> Iterator[str]:
        """跟踪一次同步操作，保留完成状态和失败发生的阶段。"""
        key = uuid4().hex
        row = {"id": key, "action": action, "name": sanitize_text(name), "stage": "start",
               "started_at": time.time(), "status": "active"}
        with self._lock:
            self._active[key] = row
        token = self._current.set(key)
        try:
            yield key
        except BaseException as exc:
            self._finish(key, "failed", str(exc) or type(exc).__name__)
            raise
        else:
            self._finish(key, "succeeded")
        finally:
            self._current.reset(token)

    def _finish(self, key: str, status: str, error: str = "") -> None:
        with self._lock:
            row = self._active.pop(key)
            row.update(status=status, at=time.time())
            self._remember(row, error)

    def _remember(self, row: dict[str, Any], error: str) -> None:
        """调用方持锁，将终态一次性发布给历史查询及动态失败提示。"""
        if error:
            row["error"] = sanitize_text(error)[:200]
            self._failures.insert(0, row)
            del self._failures[_FAILURE_CAP:]
        self._recent.appendleft(row)

    def operations(self, operation_id: str = "") -> list[dict[str, Any]]:
        """按需查询当前进程的操作；成功记录不参与模型上下文注入。"""
        with self._lock:
            return [dict(row) for row in (*reversed(self._active.values()), *self._recent)
                    if not operation_id or row["id"] == operation_id]

    def record_failure(self, action: str, name: str, error: str) -> None:
        """记录由批处理捕获的失败。"""
        now = time.time()
        row = {"id": uuid4().hex, "action": action, "name": sanitize_text(name), "stage": "start",
               "started_at": now, "at": now, "status": "failed"}
        with self._lock:
            self._remember(row, error)

    def snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        """当前状态快照：进行中操作 + 窗口内的最近失败。"""
        now = time.time()
        with self._lock:
            self._failures = [f for f in self._failures
                              if now - float(f["at"]) <= _FAILURE_TTL]
            return {
                "active": [dict(op) for op in self._active.values()],
                "failures": [dict(f) for f in self._failures],
            }


_board: Optional[OperationBoard] = None
_board_lock = threading.Lock()


def get_operation_board() -> OperationBoard:
    """全局操作状态板单例。"""
    global _board
    with _board_lock:
        if _board is None:
            _board = OperationBoard()
        return _board
