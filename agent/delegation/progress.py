"""委托执行事实：排队、模型和工具活动、耗时提醒与停止收尾。

模型可见：主 AI 经后台查询和动态尾部读取阶段、用量及提醒，不注入思考正文。
Token 影响：每个运行只增加有界状态摘要，操作证据按需读取。
缓存影响：状态和提醒位于动态区、工具结果及通知尾部，不改稳定前缀。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from agent.mind.background.tasks import BackgroundTaskRegistry


class DelegationProgress:
    """一次委托的可观察事实与总耗时提醒；计时器没有取消执行的权限。"""

    def __init__(self, task_id: str, registry: BackgroundTaskRegistry | None,
                 on_change: Callable[[], None] | None = None) -> None:
        self.task_id = task_id
        self.registry = registry
        self.phase = "queued"
        self.updated_at = self.created_at = time.time()
        self.execution_started_at = 0.0
        self.finished_at = 0.0
        self.iteration = 0
        self.model_calls = self.tool_calls = 0
        self.operation = ""
        self.overdue = False
        self.cancel_source = ""
        self.expected_seconds = 0.0
        self.usage: dict[str, int] = {}
        self._tools: dict[str, str] = {}
        self._timer: asyncio.TimerHandle | None = None
        self._on_change = on_change

    def start(self, seconds: float, stall_seconds: float = 0.0) -> None:
        """获得执行槽后开始计时，排队耗时不计入执行提醒阈值。"""
        self.phase = "preparing"
        self.updated_at = self.execution_started_at = time.time()
        self.expected_seconds = seconds
        if seconds > 0:
            self._timer = asyncio.get_running_loop().call_later(seconds, self._alert)
        if self.registry is not None and stall_seconds > 0:
            self.registry.watch_stall(self.task_id, stall_seconds)

    def _alert(self) -> None:
        self._timer = None
        if self.finished_at or self.cancel_source:
            return
        self.overdue = True
        if self._on_change is not None:
            self._on_change()
        if self.registry is not None:
            self.registry.alert_timeout(
                self.task_id,
                f"子代理执行已超过 {self.expected_seconds:g} 秒提醒阈值，仍在运行；"
                f"阶段={self.phase}，当前操作={self.operation or '无'}。超时不取消任务，请根据执行情况决定。",
            )

    def update(self, kind: str, payload: dict[str, Any]) -> None:
        """投影模型和工具事件；并行工具逐项记录，结束一个不抹掉其余活动。"""
        self.updated_at = time.time()
        if kind == "round":
            self.iteration = int(payload.get("iteration", 0)) + 1
            self.phase, self.operation = "processing", ""
        elif kind == "llm_start":
            self.model_calls += 1
            self.phase, self.operation = "model", str(payload.get("model", ""))
        elif kind in ("tool_start", "tool_end"):
            tool_id = str(payload.get("tool_id", ""))
            tool = str(payload.get("tool_name", ""))
            if kind == "tool_start":
                self.tool_calls += 1
                self._tools[tool_id] = tool
            else:
                self._tools.pop(tool_id, None)
            self.phase = "tool" if self._tools else "processing"
            self.operation = ", ".join(self._tools.values())[:200]
            if self.registry is not None:
                keys = ("tool_id", "tool_name", "arguments", "result", "error", "success",
                        "cancelled", "duration_ms", "arguments_truncated", "result_truncated")
                self.registry.record_evidence(self.task_id, kind, **{key: payload[key] for key in keys if key in payload})
        elif kind == "llm_end":
            self.phase = "tool" if self._tools else "processing"
            self.operation = ", ".join(self._tools.values())[:200]
        if self.cancel_source:
            self.phase = "stopping"

    def request_stop(self, source: str) -> None:
        """停止请求是进行中状态；执行方收尾后才发布取消终态。"""
        self.cancel_source = source
        self.phase = "stopping"
        self._cancel_timer()
        if self.registry is not None:
            self.registry.watch_stall(self.task_id, 0)
        if self._on_change is not None:
            self._on_change()

    def finish(self, phase: str) -> None:
        """冻结结束时间并回收提醒，不删除既有操作事实。"""
        self.phase = phase
        self.finished_at = time.time()
        self._cancel_timer()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def snapshot(self) -> dict[str, Any]:
        now = self.finished_at or time.time()
        return {
            "phase": self.phase, "operation": self.operation, "updated_at": self.updated_at,
            "iteration": self.iteration, "current_tool": ", ".join(self._tools.values())[:200],
            "model_calls": self.model_calls, "tool_calls": self.tool_calls, "usage": dict(self.usage),
            "execution_seconds": max(0, int(now - self.execution_started_at)) if self.execution_started_at else 0,
            "queued_seconds": max(0, int((self.execution_started_at or now) - self.created_at)),
            "idle_seconds": max(0, int(now - self.updated_at)), "expected_seconds": self.expected_seconds,
            "overdue": self.overdue, "cancel_requested": bool(self.cancel_source), "cancel_source": self.cancel_source,
        }
