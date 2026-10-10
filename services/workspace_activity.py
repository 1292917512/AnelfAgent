"""工作区实时过程投影：按轮次归并思考、工具与文件变更，限量保留近期记录。"""

from __future__ import annotations

import asyncio
import time
import uuid
from copy import deepcopy
from typing import Any

from core.activity import ACTIVITY_TEXT_LIMIT, EVENT_ACTIVITY_FINISHED, EVENT_ACTIVITY_STARTED, current_activity_id
from core.event_bus import (
    EVENT_DELEGATION_RESOLVED,
    EVENT_DELEGATION_STARTED,
    EVENT_PLAN_CANCELLED,
    EVENT_PLAN_DELETED,
    EVENT_PLAN_STATUS_CHANGED,
    EVENT_PLAN_STEP_UPDATED,
    EVENT_PLAN_SUBMITTED,
    EVENT_THINKING_LLM_END,
    EVENT_THINKING_LLM_START,
    EVENT_THINKING_TOOL_END,
    EVENT_THINKING_TOOL_START,
    event_bus,
)
from core.realtime_hub import publish
from core.sanitizer import sanitize_text
from core.stream_events import EVENT_ASSISTANT_DELTA, EVENT_FILE_DIFF
from services.activity_presentation import presentation_label, relative_workspace_path, source_reference, tool_targets

MAX_RUNS = 30
MAX_ENTRIES = 160
MAX_TEXT = ACTIVITY_TEXT_LIMIT


class WorkspaceActivityService:
    """独立于频道和详细追踪的有界实时过程，快照支持刷新和断线恢复。"""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._revision = 0
        self._epoch = uuid.uuid4().hex
        self._started = False
        self._pending: dict[str, asyncio.TimerHandle] = {}

    def start(self) -> None:
        """幂等订阅运行时过程事件。"""
        if self._started:
            return
        self._started = True
        for event, handler in (
            (EVENT_ACTIVITY_STARTED, self._start),
            (EVENT_ACTIVITY_FINISHED, self._finish),
            (EVENT_ASSISTANT_DELTA, self._delta),
            (EVENT_THINKING_LLM_START, self._llm_start),
            (EVENT_THINKING_LLM_END, self._llm_end),
            (EVENT_THINKING_TOOL_START, self._tool_start),
            (EVENT_THINKING_TOOL_END, self._tool_end),
            (EVENT_FILE_DIFF, self._file),
            (EVENT_DELEGATION_STARTED, self._delegation_start),
            (EVENT_DELEGATION_RESOLVED, self._delegation_end),
            (EVENT_PLAN_SUBMITTED, self._plan_start),
            (EVENT_PLAN_STEP_UPDATED, self._plan_step),
            (EVENT_PLAN_STATUS_CHANGED, self._plan_status),
            (EVENT_PLAN_CANCELLED, self._plan_cancel),
            (EVENT_PLAN_DELETED, self._plan_delete),
        ):
            event_bus.on(event, handler, owner="workspace-activity")

    def stop(self) -> None:
        """解除订阅并取消待发送帧。"""
        event_bus.off_by_owner("workspace-activity")
        self._started = False
        for timer in self._pending.values():
            timer.cancel()
        self._pending.clear()
        for run in self._runs.values():
            if run["status"] == "running":
                run["status"] = "interrupted"
                run["ended_at"] = time.time()
                for entry in run["entries"]:
                    if entry.get("status") in {"running", "queued"}:
                        entry["status"] = "interrupted"
                        entry["duration_ms"] = max(0, round((run["ended_at"] - entry["ts"]) * 1000))
                self._changed(run, immediate=True)

    def snapshot(self) -> dict[str, Any]:
        """返回本进程近期执行快照，不包含模型上下文与完整工具结果。"""
        return {"epoch": self._epoch, "revision": self._revision, "runs": deepcopy(list(self._runs.values()))}

    def _get(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._runs.get(str(payload.get("turn_id") or current_activity_id.get()))

    def _changed(self, run: dict[str, Any], *, immediate: bool = False) -> None:
        self._revision += 1
        run["revision"] = self._revision
        run["updated_at"] = time.time()
        run_id = run["id"]
        if immediate:
            timer = self._pending.pop(run_id, None)
            if timer:
                timer.cancel()
            self._flush(run_id)
        elif run_id not in self._pending:
            self._pending[run_id] = asyncio.get_running_loop().call_later(0.1, self._flush, run_id)

    def _flush(self, run_id: str) -> None:
        self._pending.pop(run_id, None)
        run = self._runs.get(run_id)
        if run is not None:
            publish({"event": "activity" if run["status"] == "running" else "activity_end",
                     "epoch": self._epoch, "run": deepcopy(run)})

    def _append(self, run: dict[str, Any], entry: dict[str, Any]) -> None:
        run["entry_count"] += 1
        entry.setdefault("id", f'{run["id"]}:{run["entry_count"]}')
        entry.setdefault("ts", time.time())
        entry.setdefault("generation", run["generation"])
        run["entries"].append(entry)
        if len(run["entries"]) > MAX_ENTRIES:
            index = next((i for i, item in enumerate(run["entries"]) if item.get("status") not in {"running", "queued"}), 0)
            run["entries"].pop(index)
            run["truncated"] = True

    async def _start(self, payload: dict[str, Any]) -> None:
        run_id = str(payload["turn_id"])
        if run_id in self._runs:
            return
        self._runs[run_id] = run = {
            "id": run_id, "scope": str(payload.get("scope", "")),
            "origin_scope": str(payload.get("origin_scope", "")),
            "parent_id": str(payload.get("parent_id", "")), "actor": str(payload.get("actor", "")),
            "label": presentation_label(str(payload.get("label", ""))),
            "kind": str(payload.get("kind", "reflection")), "owner_id": str(payload.get("owner_id", "")),
            "source": source_reference(str(payload.get("origin_scope") or payload.get("scope") or "")),
            "input": sanitize_text(str(payload.get("label", "")))[:MAX_TEXT],
            "generation": 0,
            "status": "running", "started_at": time.time(), "ended_at": None,
            "entries": [], "entry_count": 0, "truncated": False,
        }
        if run["owner_id"]:
            found = self._find_entry("delegation", run["owner_id"])
            if found:
                parent, entry = found
                entry.update(status="running", run_id=run_id)
                self._changed(parent)
        completed = [key for key, item in self._runs.items() if item["status"] != "running"]
        for key in completed[:max(0, len(self._runs) - MAX_RUNS)]:
            self._runs.pop(key)
        self._changed(run, immediate=True)

    async def _finish(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None:
            return
        run["status"] = str(payload["status"])
        run["error"] = sanitize_text(str(payload.get("error", "")))[:MAX_TEXT]
        run["ended_at"] = time.time()
        for entry in run["entries"]:
            if entry["kind"] in {"tool", "model"} and entry.get("status") == "running":
                entry["status"] = "interrupted"
                entry["duration_ms"] = max(0, round((run["ended_at"] - entry["ts"]) * 1000))
        self._changed(run, immediate=True)

        if run["kind"] == "delegation" and run["status"] in {"failed", "cancelled", "interrupted"}:
            await self._delegation_end({"delegation_id": run["owner_id"], "success": False,
                                        "cancelled": run["status"] != "failed", "error": run["error"]})

    async def _delta(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None or run["status"] != "running":
            return
        entries = run["entries"]
        if payload.get("reset"):
            run["entries"] = [entry for entry in entries if entry["kind"] not in {"thinking", "text"}
                              or entry.get("generation") != run["generation"]]
        else:
            if not payload.get("delta"):
                return
            kind = "thinking" if payload.get("reasoning") else "text"
            if not entries or entries[-1]["kind"] != kind:
                self._append(run, {"kind": kind, "content": ""})
            entry = run["entries"][-1]
            text = entry["content"] + str(payload.get("delta", ""))
            entry["content"] = sanitize_text(text[-MAX_TEXT:])
            entry["truncated"] = entry.get("truncated", False) or len(text) > MAX_TEXT
        self._changed(run)

    async def _llm_start(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None:
            return
        run["generation"] += 1
        self._append(run, {"kind": "model", "name": str(payload.get("model", "")), "status": "running"})
        self._changed(run)

    async def _llm_end(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None:
            return
        entries = [entry for entry in run["entries"] if entry.get("generation") == run["generation"]]
        model = next((entry for entry in entries if entry["kind"] == "model"), None)
        if model:
            model.update(status="error" if payload.get("error") else "done",
                         error=sanitize_text(str(payload.get("error", "")))[:MAX_TEXT],
                         duration_ms=payload.get("duration_ms", 0))
        for kind, field in (("thinking", "reasoning_content"), ("text", "content")):
            content = str(payload.get(field) or "")
            if content and not any(entry["kind"] == kind for entry in entries):
                self._append(run, {"kind": kind, "content": sanitize_text(content)[:MAX_TEXT],
                                   "truncated": len(content) > MAX_TEXT or bool(payload.get("reasoning_truncated" if kind == "thinking" else "content_truncated"))})
        self._changed(run)

    async def _tool_start(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None or payload.get("tool_name") == "end_reply":
            return
        self._append(run, {"id": str(payload["tool_id"]), "kind": "tool", "name": str(payload["tool_name"]),
                           "arguments": sanitize_text(str(payload.get("arguments") or payload.get("arguments_preview", "")))[:MAX_TEXT],
                           "targets": tool_targets(str(payload.get("arguments") or payload.get("arguments_preview", ""))),
                           "request_id": str((payload.get("request") or {}).get("request_id", "")),
                           "truncated": bool(payload.get("arguments_truncated")) or len(str(payload.get("arguments", ""))) > MAX_TEXT, "status": "running"})
        self._changed(run)

    async def _tool_end(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None:
            return
        entry = next((item for item in run["entries"] if item["id"] == payload.get("tool_id")), None)
        if entry is None:
            return
        result = sanitize_text(str(payload.get("result") or payload.get("result_preview") or payload.get("error") or ""))
        entry.update(status="done" if payload.get("success") else "error", result=result[:MAX_TEXT],
                     truncated=entry.get("truncated", False) or bool(payload.get("result_truncated")) or len(result) > MAX_TEXT,
                     duration_ms=payload.get("duration_ms", 0))
        self._changed(run)

    async def _file(self, payload: dict[str, Any]) -> None:
        entry = {"kind": "file", "path": relative_workspace_path(str(payload.get("path", ""))),
                 "diff": sanitize_text(str(payload.get("diff", "")))[:MAX_TEXT],
                 "move_from": relative_workspace_path(str(payload.get("move_from") or "")),
                 "binary": bool(payload.get("binary")),
                 "additions": payload.get("additions", 0), "removals": payload.get("removals", 0)}
        publish({"event": "file_diff", **entry})
        run = self._get(payload)
        if run is None:
            return
        self._append(run, entry)
        self._changed(run)

    def _find_entry(self, kind: str, entry_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for run in reversed(list(self._runs.values())):
            for entry in run["entries"]:
                if entry["kind"] == kind and entry["id"] == entry_id:
                    return run, entry
        return None

    async def _delegation_start(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None:
            return
        self._append(run, {"kind": "delegation", "id": str(payload["delegation_id"]),
                           "goal": sanitize_text(str(payload.get("goal", "")))[:2000],
                           "agent": str(payload.get("agent") or payload.get("role") or ""),
                           "status": "queued", "background": bool(payload.get("background"))})
        self._changed(run)

    async def _delegation_end(self, payload: dict[str, Any]) -> None:
        found = self._find_entry("delegation", str(payload.get("delegation_id", "")))
        if found:
            run, entry = found
            entry.update(status="cancelled" if payload.get("cancelled") else "done" if payload.get("success") else "error",
                         result=sanitize_text(str(payload.get("output") or payload.get("error") or ""))[:MAX_TEXT],
                         duration_ms=round((time.time() - entry["ts"]) * 1000))
            self._changed(run, immediate=True)

    async def _plan_start(self, payload: dict[str, Any]) -> None:
        run = self._get(payload)
        if run is None:
            return
        self._append(run, {"kind": "plan", "id": str(payload["plan_id"]),
                           "goal": sanitize_text(str(payload.get("goal", "")))[:2000],
                           "files": sanitize_text(str(payload.get("files", "")))[:2000],
                           "risks": sanitize_text(str(payload.get("risks", "")))[:2000],
                           "steps": [{"content": sanitize_text(str(s.get("content", "")))[:2000],
                                      "status": str(s.get("status", "pending")), "note": ""}
                                     for s in payload.get("steps", [])[:80]], "status": "running"})
        self._changed(run)

    async def _plan_step(self, payload: dict[str, Any]) -> None:
        found = self._find_entry("plan", str(payload.get("plan_id", "")))
        if found:
            run, entry = found
            index = payload.get("step_index", -1)
            if isinstance(index, int) and 0 <= index < len(entry["steps"]):
                entry["steps"][index].update(status=payload.get("step_status", "pending"),
                                             note=sanitize_text(str(payload.get("note", "")))[:2000])
                self._changed(run)

    async def _plan_status(self, payload: dict[str, Any]) -> None:
        found = self._find_entry("plan", str(payload.get("plan_id", "")))
        if found:
            run, entry = found
            entry["status"] = str(payload.get("goal_status", "cancelled"))
            self._changed(run, immediate=True)

    async def _plan_cancel(self, payload: dict[str, Any]) -> None:
        await self._plan_status({**payload, "goal_status": "cancelled"})

    async def _plan_delete(self, payload: dict[str, Any]) -> None:
        await self._plan_status({**payload, "goal_status": "deleted"})


workspace_activity = WorkspaceActivityService()
