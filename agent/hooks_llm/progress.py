"""钩子执行事实投影，正文留在已有的有界工作区过程流。

模型可见：当前阶段、调用计数、最近进展与关联 ID；不复制思考正文。
Token 影响：按需查询或动态尾部短摘要；缓存影响：不修改稳定前缀。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator

from core.activity import EVENT_ACTIVITY_FINISHED, EVENT_ACTIVITY_STARTED
from core.event_bus import (
    EVENT_THINKING_LLM_END,
    EVENT_THINKING_LLM_START,
    EVENT_THINKING_TOOL_END,
    EVENT_THINKING_TOOL_START,
    event_bus,
)
from core.hooks import HookRun, current_hook_run
from core.stream_events import EVENT_ASSISTANT_DELTA


class HookProgress:
    """只观察本次钩子的执行树，保留阶段事实而不持有事件正文。"""

    def __init__(self, run: HookRun) -> None:
        self.run = run
        self._tools: dict[str, str] = {}
        self.terminal_status = "completed"

    def snapshot(self) -> dict[str, Any]:
        run = self.run
        return {"hook_id": run.id, "phase": run.phase, "operation": run.operation,
                "updated_at": run.updated_at, "model_calls": run.model_calls,
                "tool_calls": run.tool_calls, "activity_ids": list(run.activity_ids)}

    async def finished(self, payload: dict[str, Any]) -> None:
        if current_hook_run.get() is self.run and payload.get("turn_id") in self.run.activity_ids:
            status = str(payload.get("status", "completed"))
            if status != "completed":
                self.terminal_status = status

    async def activity(self, payload: dict[str, Any]) -> None:
        if current_hook_run.get() is not self.run or payload.get("owner_id") != self.run.id:
            return
        run_id = str(payload["turn_id"])
        if run_id not in self.run.activity_ids:
            self.run.activity_ids = [*self.run.activity_ids, run_id][-8:]
            await self.run.publish()

    async def model_start(self, payload: dict[str, Any]) -> None:
        if current_hook_run.get() is self.run:
            self.run.model_calls += 1
            await self.update("model", str(payload.get("model", "")))

    async def model_end(self, payload: dict[str, Any]) -> None:
        if current_hook_run.get() is self.run:
            await self.update("processing", "")

    async def tool_start(self, payload: dict[str, Any]) -> None:
        if current_hook_run.get() is self.run:
            self._tools[str(payload.get("tool_id", ""))] = str(payload.get("tool_name", ""))
            self.run.tool_calls += 1
            await self.update("tool", str(payload.get("tool_name", "")))

    async def tool_end(self, payload: dict[str, Any]) -> None:
        if current_hook_run.get() is self.run:
            self._tools.pop(str(payload.get("tool_id", "")), None)
            await self.update("tool" if self._tools else "processing", next(iter(self._tools.values()), ""))

    async def delta(self, payload: dict[str, Any]) -> None:
        if current_hook_run.get() is self.run:
            self.run.updated_at = time.time()

    async def update(self, phase: str, operation: str) -> None:
        self.run.phase, self.run.operation = phase, operation
        self.run.updated_at = time.time()
        await self.run.publish()

    @contextmanager
    def observe(self) -> Iterator[None]:
        """执行期订阅，结束时解除全部回调；排队阶段不占观察器。"""
        owner = f"hook-progress:{self.run.id}"
        for event, handler in (
            (EVENT_ACTIVITY_STARTED, self.activity),
            (EVENT_ACTIVITY_FINISHED, self.finished),
            (EVENT_THINKING_LLM_START, self.model_start),
            (EVENT_THINKING_LLM_END, self.model_end),
            (EVENT_THINKING_TOOL_START, self.tool_start),
            (EVENT_THINKING_TOOL_END, self.tool_end),
            (EVENT_ASSISTANT_DELTA, self.delta),
        ):
            event_bus.on(event, handler, owner=owner)
        try:
            yield
        finally:
            event_bus.off_by_owner(owner)
