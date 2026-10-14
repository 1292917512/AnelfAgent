"""有界执行诊断，只记录现有事件的事实摘要，不参与执行决策。

模型可见：按需查询当前/近期轮次的耗时、模型用量、工具与投递回执。
Token 影响：仅查询时返回有上限的摘录，不生成额外摘要或逐轮注入。
缓存影响：不改模型输入或稳定前缀；诊断结果走普通工具结果通道。
"""

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from core.activity import (
    EVENT_ACTIVITY_CONTEXT,
    EVENT_ACTIVITY_FINISHED,
    EVENT_ACTIVITY_STARTED,
    EVENT_ACTIVITY_UPDATE,
    current_activity_id,
)
from core.event_bus import (
    EVENT_THINKING_LLM_END,
    EVENT_THINKING_LLM_START,
    EVENT_THINKING_TOOL_END,
    EVENT_THINKING_TOOL_START,
    event_bus,
)
from core.hooks import EVENT_HOOK_EXECUTION
from core.sanitizer import sanitize_text
from core.tags import etag_all
from core.tool_results import parse_tool_result_json

MAX_RUNS = 40
MAX_STEPS = 80
PREVIEW_LIMIT = 500


def preview(value: Any) -> str:
    """先脱敏再裁剪，只提供文字摘录。"""
    text = sanitize_text(str(value or ""))
    return text[:PREVIEW_LIMIT] + ("…" if len(text) > PREVIEW_LIMIT else "")


@dataclass
class _Step:
    data: dict[str, Any]
    clock: float

    def snapshot(self, now: float) -> dict[str, Any]:
        data = deepcopy(self.data)
        if data["status"] in {"running", "queued"}:
            data["duration_ms"] = round(max(0, now - self.clock) * 1000)
        return data


@dataclass
class _Run:
    data: dict[str, Any]
    clock: float
    steps: dict[tuple[str, str], _Step] = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.data["status"] == "running" or any(step.data["status"] in {"running", "queued"} for step in self.steps.values())


class ExecutionDiagnostics:
    """在事件循环内维护紧凑诊断，不存完整上下文、推理正文或流式增量。"""

    def __init__(self) -> None:
        self._runs: dict[str, _Run] = {}
        self._started = False
        self.epoch = uuid.uuid4().hex
        self.evicted = 0

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for event, handler in (
            (EVENT_ACTIVITY_STARTED, self._start), (EVENT_ACTIVITY_FINISHED, self._finish),
            (EVENT_THINKING_LLM_START, self._model_start), (EVENT_THINKING_LLM_END, self._model_end),
            (EVENT_THINKING_TOOL_START, self._tool_start), (EVENT_THINKING_TOOL_END, self._tool_end),
            (EVENT_ACTIVITY_CONTEXT, self._context), (EVENT_HOOK_EXECUTION, self._hook),
            (EVENT_ACTIVITY_UPDATE, self._update),
        ):
            event_bus.on(event, handler, owner="execution-diagnostics")

    def stop(self) -> None:
        event_bus.off_by_owner("execution-diagnostics")
        self._started = False
        self._runs.clear()
        self.evicted = 0
        self.epoch = uuid.uuid4().hex

    def scopes(self) -> list[str]:
        return list(dict.fromkeys(
            str(run.data["origin_scope"] or run.data["scope"])
            for run in reversed(list(self._runs.values()))
        ))

    def snapshot(self, *, scope: str = "", turn_id: str = "", limit: int = 3, steps: int = 12) -> dict[str, Any]:
        """按会话或轮次查询；步骤按执行顺序返回，未保留部分显式标记。"""
        now = time.monotonic()
        selected = [run for run in reversed(list(self._runs.values()))
                    if (not scope or scope in {run.data["scope"], run.data["origin_scope"]})
                    and (not turn_id or turn_id == run.data["id"])]
        current = current_activity_id.get()
        selected.sort(key=lambda run: run.data["id"] != current)
        results = []
        for run in selected[:max(1, min(limit, 10))]:
            data = deepcopy(run.data)
            if data["status"] == "running":
                data["duration_ms"] = round(max(0, now - run.clock) * 1000)
            retained = list(run.steps.values())
            data["steps"] = [step.snapshot(now) for step in retained[-max(1, min(steps, MAX_STEPS)):]]
            data["omitted_steps"] = data["step_count"] - len(data["steps"])
            data["active_steps"] = [step.snapshot(now) for step in retained if step.data["status"] in {"running", "queued"}]
            results.append(data)
        return {"epoch": self.epoch, "observed_at": time.time(), "collecting": self._started, "current_turn_id": current,
                "retention": {"runs": MAX_RUNS, "steps_per_run": MAX_STEPS, "evicted_runs": self.evicted},
                "matching_runs": len(selected), "active_run_ids": [run.data["id"] for run in selected if run.is_active()], "runs": results}

    def _get(self, payload: dict[str, Any]) -> _Run | None:
        return self._runs.get(str(payload.get("turn_id") or current_activity_id.get()))

    def _step(self, run: _Run, key: str, kind: str, name: str, payload: dict[str, Any]) -> _Step:
        identity = (kind, key)
        if identity in run.steps:
            return run.steps[identity]
        step = _Step({"id": key, "kind": kind, "name": preview(name), "status": "running",
                      "started_at": time.time(), "duration_ms": 0,
                      "request_id": str((payload.get("request") or {}).get("request_id", ""))}, clock=time.monotonic())
        run.steps[identity] = step
        run.data["step_count"] += 1
        while len(run.steps) > MAX_STEPS:
            oldest = next((key for key, item in run.steps.items() if item.data["status"] not in {"running", "queued"}), next(iter(run.steps)))
            run.steps.pop(oldest)
        return step

    async def _start(self, payload: dict[str, Any]) -> None:
        key = str(payload["turn_id"])
        if key in self._runs:
            return
        self._runs[key] = _Run({
            "id": key, "scope": str(payload.get("scope", "")), "origin_scope": str(payload.get("origin_scope", "")),
            "parent_id": str(payload.get("parent_id", "")), "kind": str(payload.get("kind", "reflection")),
            "trigger": preview(payload.get("trigger")), "label": preview(payload.get("label")),
            "status": "running", "started_at": time.time(), "duration_ms": 0, "step_count": 0,
        }, clock=time.monotonic())
        while len(self._runs) > MAX_RUNS:
            oldest = next((key for key, run in self._runs.items() if not run.is_active()), next(iter(self._runs)))
            self._runs.pop(oldest)
            self.evicted += 1

    async def _finish(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None:
            return
        now = time.monotonic()
        run.data.update(status=payload["status"], duration_ms=round(max(0, now - run.clock) * 1000),
                        error=preview(payload.get("error")))
        for step in run.steps.values():
            if step.data["kind"] != "hook" and step.data["status"] in {"running", "queued"}:
                step.data.update(status="interrupted", duration_ms=round(max(0, now - step.clock) * 1000))

    async def _model_start(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is not None and payload.get("call_id"):
            step = self._step(run, payload["call_id"], "model", payload.get("model", ""), payload)
            step.data["purpose"] = preview(payload.get("purpose"))

    async def _model_end(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        step = run.steps.get(("model", payload.get("call_id", ""))) if run else None
        if step is None:
            return
        usage = payload.get("usage") or {}
        step.data.update(
            name=preview(payload.get("model")), status="failed" if payload.get("error") else "completed",
            duration_ms=payload.get("duration_ms", 0), ttft_ms=payload.get("ttft_ms"),
            error=preview(payload.get("error")), preview=preview(payload.get("content") or payload.get("content_preview")),
            truncated=bool(payload.get("content_truncated")) or len(str(payload.get("content") or payload.get("content_preview") or "")) > PREVIEW_LIMIT,
            usage={key: usage[key] for key in ("total_input_tokens", "completion_tokens", "cache_read_input_tokens", "cache_observable") if key in usage},
        )

    async def _tool_start(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is not None and payload.get("tool_id"):
            step = self._step(run, payload["tool_id"], "tool", payload.get("tool_name", ""), payload)
            step.data["arguments"] = preview(payload.get("arguments") or payload.get("arguments_preview"))
            step.data["truncated"] = bool(payload.get("arguments_truncated")) or len(str(payload.get("arguments") or payload.get("arguments_preview") or "")) > PREVIEW_LIMIT

    async def _tool_end(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        step = run.steps.get(("tool", payload.get("tool_id", ""))) if run else None
        if step is None:
            return
        result = ("" if step.data["name"] == "get_conversation_diagnostics"
                  else str(payload.get("result") or payload.get("result_preview") or ""))
        step.data.update(status="completed" if payload.get("success") else "failed",
                         duration_ms=payload.get("duration_ms", 0), preview=preview(result), error=preview(payload.get("error")),
                         truncated=step.data.get("truncated", False) or bool(payload.get("result_truncated")) or len(result) > PREVIEW_LIMIT)
        parsed = parse_tool_result_json(result)
        if isinstance(parsed, dict) and isinstance(parsed.get("delivery"), str):
            step.data["delivery"] = self._delivery(parsed["delivery"])

    @staticmethod
    def _delivery(text: str) -> dict[str, str]:
        return {key.removeprefix("delivery_"): preview(value) for key, value in etag_all(text)
                if key in {"delivery_mode", "delivery_status", "delivery_scope"}}

    async def _context(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None:
            return
        step = self._step(run, "context", "context", "context", payload)
        step.data.update(status="completed" if payload["status"] == "done" else payload["status"],
                         duration_ms=payload.get("duration_ms", 0), error=preview(payload.get("error")))

    async def _hook(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None:
            return
        step = self._step(run, str(payload["id"]), "hook", payload.get("name", ""), payload)
        step.data.update(status=payload["status"], duration_ms=payload.get("duration_ms", 0),
                         preview=preview(payload.get("detail")), mode=payload.get("mode", ""),
                         phase=preview(payload.get("phase")), operation=preview(payload.get("operation")))

    async def _update(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None or payload.get("origin") != "delivery":
            return
        for item in payload.get("items", [])[:20]:
            content = str(item.get("content", ""))
            receipt = self._delivery(content)
            if not receipt:
                continue
            step = self._step(run, uuid.uuid4().hex, "delivery", "delivery", payload)
            step.data.update(status=receipt.get("status", "unknown"), delivery=receipt, preview=preview(content))


execution_diagnostics = ExecutionDiagnostics()
