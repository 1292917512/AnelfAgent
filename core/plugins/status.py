"""插件操作状态板 — 进行中操作与最近失败的轻量事实记录。

PluginManager 的变更操作经 track_operation 埋点；上下文提供者
（entities/plugins）每轮读取快照注入 volatile 层——有进行中/失败事项
才产生一行文本，常态为空零占用。
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

# 失败记录保留窗口（秒）：超过即视为已处置，不再占用上下文
_FAILURE_TTL = 1800.0
# 失败记录条数上限
_FAILURE_CAP = 5


class OperationBoard:
    """插件操作状态板（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        # op_id -> {action, name, started_at}
        self._active: Dict[int, Dict[str, Any]] = {}
        # 最近失败：[{action, name, error, at}]，新的在前
        self._failures: List[Dict[str, Any]] = []

    @contextmanager
    def track(self, action: str, name: str) -> Iterator[None]:
        """记录一次操作的开始/结束；异常时记入最近失败。"""
        with self._lock:
            self._seq += 1
            op_id = self._seq
            self._active[op_id] = {"action": action, "name": name,
                                   "started_at": time.time()}
        try:
            yield
        except Exception as e:
            with self._lock:
                self._failures.insert(0, {
                    "action": action, "name": name,
                    "error": str(e).strip()[:200], "at": time.time(),
                })
                del self._failures[_FAILURE_CAP:]
            raise
        finally:
            with self._lock:
                self._active.pop(op_id, None)

    def record_failure(self, action: str, name: str, error: str) -> None:
        """直接记录一次失败（供内部捕获异常的批处理路径使用）。"""
        with self._lock:
            self._failures.insert(0, {
                "action": action, "name": name,
                "error": error.strip()[:200], "at": time.time(),
            })
            del self._failures[_FAILURE_CAP:]

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
