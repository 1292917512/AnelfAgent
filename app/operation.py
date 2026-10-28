"""可取消安装操作的阶段、耗时及真实下载进度，不估算虚假百分比。"""

from __future__ import annotations

import threading
import time
from typing import Any


class Cancelled(RuntimeError):
    """用户取消安装，已完成的文件保留以便重试。"""


class Operation:
    """线程安全的单次操作状态；只允许取消安装和更新子进程。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._value: dict[str, Any] = {}

    def begin(self, action: str) -> None:
        with self._lock:
            self._cancel.clear()
            self._value = {"action": action, "status": "running", "stage": "", "detail": "",
                           "started_at": time.time(), "updated_at": time.time(), "steps": [],
                           "cancellable": action in {"setup", "install", "update"}, "cancelling": False}

    def check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled("Installation cancelled. Completed files were kept; retry to continue.")

    def cancel(self) -> None:
        with self._lock:
            if not self._value.get("cancellable"):
                raise RuntimeError("There is no cancellable installation")
            self._value["cancelling"] = True
            self._cancel.set()

    def stage(self, key: str) -> None:
        self.check()
        with self._lock:
            for step in self._value.get("steps", []):
                if step["status"] == "running":
                    step["status"] = "done"
            self._value.setdefault("steps", []).append({"key": key, "status": "running"})
            self._value.update(stage=key, detail="", received=0, total=0, progress=None,
                               stage_started_at=time.time(), updated_at=time.time())

    def update(self, detail: str, received: int = 0, total: int = 0, *, existing_packages: int | None = None) -> None:
        with self._lock:
            self._value.update(detail=detail[-2000:], received=received, total=total, updated_at=time.time())
            if existing_packages is not None:
                self._value["existing_packages"] = existing_packages

    def report(self, progress: dict[str, Any]) -> None:
        """保存结构化工具回执，与原始诊断日志分开供界面展示。"""
        with self._lock:
            self._value.update(progress=dict(progress), updated_at=time.time())

    def finish(self, status: str) -> None:
        with self._lock:
            for step in self._value.get("steps", []):
                if step["status"] == "running":
                    step["status"] = "done" if status == "done" else status
            self._value.update(status=status, cancellable=False, cancelling=False, finished_at=time.time())

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {**self._value, "steps": [dict(step) for step in self._value.get("steps", [])]}
