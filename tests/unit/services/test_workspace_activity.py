"""全局过程归并、并发归属与终态恢复。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.activity import activity_owner, activity_scope, current_activity_id
from core.event_bus import event_bus
from core.realtime_hub import TERMINAL_EVENTS
from core.tags import tag_label
from services.activity_presentation import tool_targets
from services.workspace_activity import WorkspaceActivityService


async def test_context_and_blocked_tools_are_visible_with_terminal_truth(activity):
    service, _ = activity
    async with activity_scope("run", "user_qq:1"):
        await event_bus.emit("activity_context", {"status": "running"})
        await event_bus.emit("activity_context", {"status": "done", "duration_ms": 250,
            "block_count": 1, "blocks": [{"layer": "relation", "label": "关联", "content": "[uid:1] 证据"}]})
        await event_bus.emit("thinking_tool_end", {"tool_id": "denied", "tool_name": "inspect",
            "arguments": '{"target":"user_qq:1"}', "success": False, "result": '{"error":"禁止调用","hint":"调整参数"}'})
    entries = service.snapshot()["runs"][0]["entries"]
    assert len(entries) == 2
    assert entries[0]["duration_ms"] == 250
    assert entries[0]["blocks"][0]["content"] == "[uid:1] 证据"
    assert entries[1]["status"] == "error"
    assert "调整参数" in entries[1]["result"]


async def test_child_without_final_output_is_not_marked_completed(activity):
    service, _ = activity
    async with activity_scope("parent", "user_qq:1"):
        await event_bus.emit("delegation_started", {"delegation_id": "child", "goal": "验证"})
        with activity_owner(kind="delegation", label="验证", owner_id="child"):
            async with activity_scope("child-run", "reflect:child"):
                pass
        await event_bus.emit("delegation_resolved", {"delegation_id": "child", "success": False, "error": "未提交总结"})
    runs = service.snapshot()["runs"]
    assert runs[0]["entries"][0]["status"] == "error"
    assert runs[1]["status"] == "failed"
    assert runs[1]["error"] == "未提交总结"


@pytest.fixture
def activity(monkeypatch):
    frames = []
    monkeypatch.setattr("services.workspace_activity.publish", frames.append)
    service = WorkspaceActivityService()
    service.start()
    yield service, frames
    service.stop()


async def test_parallel_and_nested_runs_keep_their_own_tools(activity):
    service, frames = activity

    async def work(run_id, scope):
        async with activity_scope(run_id, scope):
            await event_bus.emit("assistant_delta", {"turn_id": run_id, "delta": scope, "reasoning": True})
            await event_bus.emit("thinking_tool_start", {"tool_id": "same-call-id", "tool_name": "inspect", "arguments": '{"target":"group_qq:42"}'})
            await asyncio.sleep(0)
            await event_bus.emit("thinking_tool_end", {"tool_id": "same-call-id", "success": True, "result": scope, "duration_ms": 42})

    async with activity_scope("parent", "group_qq:42"):
        with activity_owner(kind="delegation", label="Validate deployment", owner_id="child", scope="group_qq:42"):
            await asyncio.gather(work("a", "reflect_a"), work("b", "reflect_b"))
    assert current_activity_id.get() == ""
    runs = {run["id"]: run for run in service.snapshot()["runs"]}
    for key in ("a", "b"):
        assert runs[key]["parent_id"] == "parent"
        assert runs[key]["kind"] == "delegation"
        assert runs[key]["source"]["channel"] == "qq"
        assert runs[key]["entries"][-1]["result"] == f"reflect_{key}"
    assert runs["parent"]["entries"] == []
    assert frames[-1]["event"] == "activity_end"
    assert "activity_end" in TERMINAL_EVENTS


async def test_fallback_reset_only_replaces_current_generation(activity):
    service, _ = activity
    async with activity_scope("run", "user_webui:web_user#chat1"):
        await event_bus.emit("thinking_llm_start", {"model": "fast"})
        await event_bus.emit("assistant_delta", {"delta": "Earlier thought", "reasoning": True})
        await event_bus.emit("thinking_llm_end", {"reasoning_content": "Earlier thought", "duration_ms": 3})
        await event_bus.emit("thinking_llm_start", {"model": "fast"})
        await event_bus.emit("assistant_delta", {"delta": "Partial output"})
        await event_bus.emit("assistant_delta", {"reset": True})
        await event_bus.emit("thinking_llm_end", {"content": "Complete fallback", "reasoning_content": "Final thought", "duration_ms": 5})
    entries = service.snapshot()["runs"][0]["entries"]
    assert [entry["content"] for entry in entries if "content" in entry] == ["Earlier thought", "Final thought", "Complete fallback"]


async def test_cancel_and_error_close_running_tools(activity, monkeypatch):
    service, _ = activity
    clock = 1000.0
    monkeypatch.setattr("services.workspace_activity.time.time", lambda: clock)
    with pytest.raises(asyncio.CancelledError):
        async with activity_scope("cancel", "group_qq:1"):
            await event_bus.emit("thinking_tool_start", {"tool_id": "tool", "tool_name": "sleep"})
            clock += 2.5
            raise asyncio.CancelledError
    with pytest.raises(ValueError):
        async with activity_scope("error", "group_qq:2"):
            raise ValueError("model unavailable")
    runs = service.snapshot()["runs"]
    assert runs[0]["status"] == "cancelled"
    assert runs[0]["entries"][0]["status"] == "interrupted"
    assert runs[0]["entries"][0]["duration_ms"] == 2500
    assert runs[1]["status"] == "failed"
    assert "model unavailable" in runs[1]["error"]
    assert current_activity_id.get() == ""


async def test_background_delegation_outlives_parent_and_plan_keeps_steps(activity):
    service, _ = activity
    async with activity_scope("parent", "group_qq:1"):
        await event_bus.emit("plan_submitted", {"plan_id": "plan", "goal": "Review", "steps": [{"content": "Inspect"}]})
        await event_bus.emit("plan_step_updated", {"plan_id": "plan", "step_index": 0, "step_status": "completed", "note": "Verified"})
        await event_bus.emit("plan_status_changed", {"plan_id": "plan", "goal_status": "completed"})
        await event_bus.emit("delegation_started", {"delegation_id": "agent", "goal": "Review asynchronously", "background": True})
        with activity_owner(kind="delegation", label="Review asynchronously", owner_id="agent"):
            async with activity_scope("child", "reflect_1"):
                pass
    assert service.snapshot()["runs"][0]["entries"][1]["status"] == "running"
    await event_bus.emit("delegation_resolved", {"delegation_id": "agent", "success": True, "output": "Checked"})
    entries = service.snapshot()["runs"][0]["entries"]
    assert entries[0]["steps"][0]["note"] == "Verified"
    assert entries[1]["result"] == "Checked"


async def test_file_changes_do_not_depend_on_web_channel(activity, monkeypatch, tmp_path):
    service, frames = activity
    monkeypatch.setattr("services.filesystem.workspace_root", lambda: str(tmp_path))
    async with activity_scope("file", "group_any_channel:42"):
        await event_bus.emit("file_diff", {"path": str(tmp_path / "new.txt"), "move_from": str(tmp_path / "old.txt"), "diff": "", "binary": True})
    frame = next(frame for frame in frames if frame["event"] == "file_diff")
    assert frame["path"] == "new.txt"
    assert frame["move_from"] == "old.txt"
    assert frame["binary"] is True
    assert service.snapshot()["runs"][0]["entries"][0]["path"] == "new.txt"


async def test_snapshot_is_detached_and_subscription_is_idempotent(activity):
    service, _ = activity
    async with activity_scope("run", "group_qq:42"):
        service.start()
        await event_bus.emit("assistant_delta", {"delta": "once"})
        snapshot = service.snapshot()
        snapshot["runs"][0]["entries"].clear()
        assert service.snapshot()["runs"][0]["entries"][0]["content"] == "once"
        assert service.snapshot()["runs"][0]["status"] == "running"


async def test_process_records_and_content_are_bounded(activity):
    from services.workspace_activity import MAX_RUN_TEXT, MAX_RUNS, _text_size

    service, _ = activity
    for index in range(MAX_RUNS + 10):
        await service._start({"turn_id": str(index), "scope": "user_test:1"})
    assert len(service.snapshot()["runs"]) == MAX_RUNS
    for index in range(40):
        await service._tool_end({"turn_id": str(MAX_RUNS + 9), "tool_id": f"tool-{index}",
                                 "tool_name": "read_file", "result": "x" * 16000, "success": True})
    current = service.snapshot()["runs"][-1]
    assert current["truncated"] is True
    assert _text_size(current["entries"]) <= MAX_RUN_TEXT
    restarted = WorkspaceActivityService()
    assert restarted.snapshot()["runs"] == []
    assert restarted.snapshot()["epoch"] != service.snapshot()["epoch"]


def test_targets_decode_tags_without_inventing_routes():
    tags = tag_label("channel", "custom") + tag_label("name", "one[two]\\three")
    targets = tool_targets(json.dumps({"target": tags, "message": "[uid:fake]", "path": "notes/a.md"}))
    assert targets == [{"key": "channel", "value": "custom"}, {"key": "name", "value": "one[two]\\three"}, {"key": "path", "value": "notes/a.md"}]


async def test_structured_tool_failure_is_rendered_as_failure(activity, monkeypatch):
    from agent.llm.types import ToolCall
    from agent.mind.tools.think_loop import execute_one_tool

    service, _ = activity
    monkeypatch.setattr("agent.mind.tools.think_loop.check_tool_permission", AsyncMock(return_value=None))
    mind = SimpleNamespace(_set_phase=lambda _: None, tool_executor=AsyncMock(return_value='{"success":false,"error":"Invalid destination","retryable":false}'), memory_store=None)
    async with activity_scope("failure", "group_qq:42"):
        await execute_one_tool(mind, ToolCall(id="send", name="send_message", arguments='{"target":"missing"}'), 0)
    entry = service.snapshot()["runs"][0]["entries"][0]
    assert entry["status"] == "error"
    assert "Invalid destination" in entry["result"]
    assert "retryable" in entry["result"]


async def test_cancelled_child_closes_parent_delegation_entry(activity):
    service, _ = activity
    async with activity_scope("parent", "group_qq:42"):
        await event_bus.emit("delegation_started", {"delegation_id": "child", "goal": "Review"})
        with activity_owner(kind="delegation", label="Review", owner_id="child"):
            with pytest.raises(asyncio.CancelledError):
                async with activity_scope("child-run", "reflect_1"):
                    raise asyncio.CancelledError
    entry = service.snapshot()["runs"][0]["entries"][0]
    assert entry["status"] == "cancelled"
