"""全局过程归并、并发归属与终态恢复。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.activity import activity_owner, activity_scope, current_activity_id
from core.event_bus import event_bus
from core.hooks import HookJudgment, HookMode, hook_execution, hook_journal
from core.realtime_hub import TERMINAL_EVENTS
from core.tags import tag_label
from services.activity_presentation import tool_presentation_owner, tool_targets
from services.workspace_activity import WorkspaceActivityService


async def test_background_completion_updates_original_turn_after_reply_finishes(activity):
    from agent.mind.background_tasks import BackgroundTaskRegistry

    service, _ = activity
    registry = BackgroundTaskRegistry()
    released = asyncio.Event()

    async def operation():
        await released.wait()
        return '{"success": true, "description": "image result"}'

    async with activity_scope("origin", "user_webui:1#a"):
        task_id = registry.submit("user_webui:1#a", "vision", "read image", operation)
        await asyncio.sleep(0)
    assert service.snapshot()["runs"][0]["entries"][0]["status"] == "running"
    async with activity_scope("other", "user_qq:2"):
        released.set()
        await registry.wait_any("user_webui:1#a", 2, task_id=task_id)
        await asyncio.sleep(0)
    runs = {run["id"]: run for run in service.snapshot()["runs"]}
    assert runs["origin"]["entries"][0]["status"] == "done"
    assert "image result" in runs["origin"]["entries"][0]["result"]
    assert runs["other"]["entries"] == []
    await registry.close()


async def test_threaded_background_registration_and_completion_publish_on_bound_loop(activity):
    from agent.mind.background_tasks import BackgroundTaskRegistry

    service, _ = activity
    registry = BackgroundTaskRegistry(loop=asyncio.get_running_loop())
    async with activity_scope("shell-origin", "user_webui:1"):
        task_id = await asyncio.to_thread(registry.register, "user_webui:1", "shell", "threaded shell")
        await asyncio.to_thread(registry.complete, task_id, True, "done")
        await asyncio.sleep(0)
    entry = service.snapshot()["runs"][0]["entries"][0]
    assert entry["status"] == "done" and entry["id"] == task_id
    await registry.close()


async def test_trigger_and_sender_remain_local_to_concurrent_runs(activity):
    service, _ = activity

    async def conversation():
        async with activity_scope("chat", "group_qq:42", kind="conversation", trigger="chat",
                                  initiator={"id": "7", "name": "Operator"}):
            await asyncio.sleep(0)
            async with activity_scope("child", "reflect:child"):
                await asyncio.sleep(0)

    async def task():
        with activity_owner(kind="task", label="Inspect", owner_id="inspect", trigger="scheduled"):
            async with activity_scope("task", "reflect:task"):
                await asyncio.sleep(0)

    await asyncio.gather(conversation(), task())
    runs = {run["id"]: run for run in service.snapshot()["runs"]}
    assert runs["chat"]["initiator"] == {"id": "7", "name": "Operator"}
    assert runs["chat"]["trigger"] == "chat"
    assert runs["chat"]["source"]["target"] == "42"
    assert runs["task"]["trigger"] == "scheduled"
    assert runs["task"]["kind"] == "task"
    assert runs["task"]["initiator"] is None
    assert runs["child"]["initiator"] is None
    assert runs["child"]["parent_id"] == "chat"
    assert runs["child"]["trigger"] == ""


async def test_context_updates_preserve_source_and_only_observe_new_cross_messages(activity):
    from copy import deepcopy

    from agent.context.observation import emit_context_update, emit_cross_channel_updates
    from core.activity import ACTIVITY_TEXT_LIMIT

    service, frames = activity
    messages = [{"role": "user", "content": "[channel:qq][uid:1] 新消息",
                 "_source": {"origin": "cross_channel_narrative", "scope": "user_qq:1", "id": 2,
                             "ts_ns": 1000000000, "role": "user"}}]
    original = deepcopy(messages)
    seen = {}
    async with activity_scope("run", "user_webui:1#main"):
        await emit_cross_channel_updates(messages, seen, 1)
        await emit_cross_channel_updates(messages, seen, 2)
        async with activity_scope("child", "reflect:child"):
            await emit_context_update([{"role": "system", "content": "[push:devops] 完成"}],
                                      origin="push", scope="user_webui:1#main", iteration=3)
        await emit_context_update([{"role": "user", "content": "x" * (ACTIVITY_TEXT_LIMIT + 5)}],
                                  origin="conversation", scope="user_webui:1#main", iteration=3, deferred=2)
    assert messages == original
    runs = {run["id"]: run for run in service.snapshot()["runs"]}
    entries = runs["run"]["entries"]
    assert len(entries) == 2
    assert entries[0]["kind"] == "notice" and entries[0]["iteration"] == 1
    assert entries[0]["items"][0]["source"]["scope"] == "user_qq:1"
    assert entries[0]["items"][0]["role"] == "user"
    assert entries[0]["items"][0]["message_id"] == "2"
    assert entries[1]["truncated"] and entries[1]["deferred"] == 2
    assert len(entries[1]["items"][0]["content"]) == ACTIVITY_TEXT_LIMIT
    assert runs["child"]["entries"][0]["origin"] == "push"
    assert frames


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


async def test_skill_facts_survive_entry_eviction_and_only_promote_successful_reads(activity, monkeypatch):
    service, _ = activity
    monkeypatch.setattr("services.workspace_activity.MAX_ENTRIES", 2)
    async with activity_scope("skill-run", "user_qq:1"):
        mcp_state = {"name": "deploy", "configured": True, "enabled": True, "connected": True, "activation": "sleeping", "tool_count": 2}
        facts = [{"name": "deploy-check", "mode": "candidate", "description": "Inspect first", "score": 0.4,
                  "mcp_status": [{**mcp_state, "url": "must-not-be-projected", "api_key": "must-not-be-projected"}]},
                 {"name": "not-used", "mode": "candidate", "description": "Candidate only"}]
        await event_bus.emit("activity_context", {"status": "done", "skills": facts})
        for call, name, success, result in [
            ("running", "not-used", None, ""), ("failure", "not-used", False, '{"error":"Missing"}'),
            ("read", "deploy-check", True, '{"ok":true,"name":"deploy-check","content":"long'),
        ]:
            await event_bus.emit("thinking_tool_start", {"tool_id": call, "tool_name": "get_skill", "arguments": json.dumps({"name": name})})
            if success is not None:
                await event_bus.emit("thinking_tool_end", {"tool_id": call, "success": success, "result": result, "result_truncated": success})
        await event_bus.emit("activity_context", {"status": "done", "skills": facts})
    run = service.snapshot()["runs"][0]
    assert len(run["entries"]) == 2 and run["truncated"]
    assert run["skills"][0] == {"name": "deploy-check", "mode": "read", "description": "Inspect first", "score": 0.4, "state": "", "truncated": True, "mcp_status": [mcp_state]}
    assert run["skills"][1]["mode"] == "candidate"
    assert facts[0]["mode"] == "candidate"


async def test_skill_summary_has_a_per_run_bound(activity):
    service, _ = activity
    async with activity_scope("bound", "reflect:test"):
        for batch in range(4):
            await event_bus.emit("activity_context", {"status": "done", "skills": [
                {"name": f"skill-{batch}-{i}", "mode": "candidate", "description": "x" * 1000}
                for i in range(16)]})
    run = service.snapshot()["runs"][0]
    assert len(run["skills"]) == 32 and run["truncated"]
    assert all(len(skill["description"]) == 300 for skill in run["skills"])


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


async def test_background_hook_keeps_parent_until_finished(activity):
    service, _ = activity
    started, release = asyncio.Event(), asyncio.Event()

    async def work():
        async with hook_execution("review", "after_reply", HookMode.ASYNC):
            started.set()
            await release.wait()

    async with activity_scope("hook-parent", "user_qq:1", kind="conversation"):
        task = asyncio.create_task(work())
        await started.wait()
    async with activity_scope("next-turn", "user_webui:1", kind="conversation"):
        pass
    snapshot = service.clear_history()
    assert {run["id"] for run in snapshot["runs"]} == {"hook-parent", "next-turn"}
    release.set()
    await task
    parent = next(run for run in service.snapshot()["runs"] if run["id"] == "hook-parent")
    assert parent["entries"][0]["status"] == "done"
    assert parent["entries"][0]["mode"] == "async"


async def test_judgment_records_survive_hook_updates_with_scope_isolation(activity):
    service, _ = activity

    async def work(scope):
        async with activity_scope(scope, scope):
            async with hook_execution("review", "after_reply", HookMode.ASYNC) as hook:
                for index in range(10):
                    item = HookJudgment(str(index), "Jev", scope, 1, 0.1, status="skip", duration_ms=24)
                    item.reason = 'password="do-not-expose"'
                    hook.judgments.append(item)
                    await hook.publish()
                    await asyncio.sleep(0)
                hook.phase, hook.operation, hook.detail = "model", "get_skill", "Done"
                await hook.publish()
                return hook.id

    ids = await asyncio.gather(work("user_qq:42"), work("user_webui:42"))
    runs = service.snapshot()["runs"]
    for run in runs:
        entry = run["entries"][0]
        assert entry["status"] == "done" and entry["detail"] == "Done"
        assert len(entry["judgments"]) == 8
        assert all(item["task"] == run["scope"] and item["status"] == "skip" for item in entry["judgments"])
        assert "do-not-expose" not in str(entry)
    recent = hook_journal.snapshot()["recent"]
    assert all(any(item["id"] == key and len(item["judgments"]) == 8 for item in recent) for key in ids)


@pytest.fixture
def activity(monkeypatch):
    frames = []
    monkeypatch.setattr("services.workspace_activity.publish", lambda frame, **_: frames.append(frame))
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


async def test_slow_subscriber_receives_latest_completion_and_independent_runs(activity, monkeypatch):
    from core import realtime_hub

    service, _ = activity
    monkeypatch.setattr("services.workspace_activity.publish", realtime_hub.publish)
    sub = realtime_hub.subscribe()
    try:
        async with activity_scope("slow", "group_qq:42"):
            await event_bus.emit("thinking_tool_start", {"tool_id": "tool", "tool_name": "inspect"})
            async with activity_scope("other", "user_webui:1"):
                await event_bus.emit("assistant_delta", {"delta": "Other channel"})
            realtime_hub.publish({"event": "reply", "content": "Delivered"})
            for _ in range(300):
                await event_bus.emit("assistant_delta", {"delta": "x"})
                if timer := service._pending.get("slow"):
                    timer.cancel()
                service._flush("slow")
            await event_bus.emit("thinking_tool_end", {"tool_id": "tool", "success": True, "result": "Done"})
        frames = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]
        assert [frame["event"] for frame in frames] == ["activity_end", "reply", "activity_end"]
        assert frames[0]["run"]["id"] == "other"
        assert frames[-1]["run"] == next(run for run in service.snapshot()["runs"] if run["id"] == "slow")
        assert frames[-1]["run"]["entries"][0]["status"] == "done"
        assert not sub.dead
    finally:
        realtime_hub.unsubscribe(sub)


async def test_fallback_reset_only_replaces_current_generation(activity):
    service, _ = activity
    async with activity_scope("run", "user_webui:web_user#chat1"):
        await event_bus.emit("thinking_llm_start", {"model": "fast"})
        await event_bus.emit("assistant_delta", {"delta": "Earlier thought", "reasoning": True})
        await event_bus.emit("thinking_llm_end", {"reasoning_content": "Earlier thought", "duration_ms": 3, "ttft_ms": 1,
            "usage": {"total_input_tokens": 100, "cache_read_input_tokens": 90, "cache_observable": True}})
        await event_bus.emit("thinking_llm_start", {"model": "fast"})
        await event_bus.emit("assistant_delta", {"delta": "Partial output"})
        await event_bus.emit("assistant_delta", {"reset": True})
        await event_bus.emit("thinking_llm_end", {"content": "Complete fallback", "reasoning_content": "Final thought", "duration_ms": 5})
    entries = service.snapshot()["runs"][0]["entries"]
    assert [entry["content"] for entry in entries if "content" in entry] == ["Earlier thought", "Final thought", "Complete fallback"]
    models = [entry for entry in entries if entry["kind"] == "model"]
    assert models[0]["ttft_ms"] == 1 and models[1]["ttft_ms"] is None
    assert models[0]["usage"]["cache_read_input_tokens"] == 90


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


async def test_clear_history_keeps_current_family_and_concurrent_work(activity, monkeypatch):
    service, frames = activity
    clock = 1.0
    monkeypatch.setattr("services.workspace_activity.time.time", lambda: clock)
    async with activity_scope("previous", "group_qq:1"):
        clock = 2.0
    clock = 10.0
    async with activity_scope("parent", "group_qq:1"):
        await event_bus.emit("thinking_tool_end", {"tool_id": "done", "tool_name": "inspect", "success": True, "result": "Keep this result"})
        clock = 11.0
    clock = 12.0
    async with activity_scope("parallel", "group_telegram:1"):
        clock = 14.0
    clock = 16.0
    await service._start({"turn_id": "child", "parent_id": "parent", "scope": "reflect:child"})
    snapshot = service.clear_history()
    assert [run["id"] for run in snapshot["runs"]] == ["parent", "parallel", "child"]
    assert snapshot["runs"][0]["entries"][0]["result"] == "Keep this result"
    assert snapshot["runs"][-1]["status"] == "running"
    assert frames[-1]["event"] == "activity_snapshot"
    assert "activity_snapshot" in TERMINAL_EVENTS
    await service._tool_end({"turn_id": "child", "tool_id": "continued", "tool_name": "inspect", "success": True, "result": "Still running"})
    assert service.snapshot()["runs"][-1]["entries"][-1]["result"] == "Still running"


async def test_clear_history_keeps_last_completed_round(activity, monkeypatch):
    service, _ = activity
    for clock in (1.0, 2.0, 3.0):
        monkeypatch.setattr("services.workspace_activity.time.time", lambda: clock)
        async with activity_scope(str(clock), "group_qq:1"):
            pass
    snapshot = service.clear_history()
    assert [run["id"] for run in snapshot["runs"]] == ["3.0"]
    assert service.clear_history() == snapshot


async def test_long_background_run_does_not_keep_unrelated_finished_history(activity, monkeypatch):
    service, _ = activity
    monkeypatch.setattr("services.workspace_activity.time.time", lambda: 1.0)
    await service._start({"turn_id": "background", "scope": "reflect:job"})
    for clock in (2.0, 3.0, 4.0):
        monkeypatch.setattr("services.workspace_activity.time.time", lambda: clock)
        async with activity_scope(str(clock), "user_webui:1"):
            pass
    assert [run["id"] for run in service.clear_history()["runs"]] == ["background", "4.0"]


@pytest.mark.parametrize(("module", "expected"), [
    ("entities.filesystem.tools", "entity:filesystem"),
    ("channels.demo.tools", "channel:demo"),
    ("agent.mind.tools.memory", ""),
])
def test_tool_render_owner_comes_from_registration_not_arguments(monkeypatch, module, expected):
    from core.entity import EntityRegistry

    def handler():
        pass

    handler.__module__ = module
    monkeypatch.setattr(EntityRegistry, "get", lambda name: SimpleNamespace(func=handler, entity_class=None))
    assert tool_presentation_owner("any_tool") == expected


@pytest.mark.parametrize(("source", "expected"), [("channel.demo", "channel:demo"), ("channel.auto", "")])
def test_channel_bridge_owner_uses_registered_source(monkeypatch, source, expected):
    from core.entity import EntityRegistry

    def handler():
        pass

    handler.__module__ = "agent.channel.tool_bridge"
    monkeypatch.setattr(EntityRegistry, "get", lambda name: SimpleNamespace(func=handler, source=source))
    assert tool_presentation_owner("specific_channel_tool") == expected


async def test_structured_tool_failure_is_rendered_as_failure(activity, monkeypatch):
    from agent.llm.types import ToolCall
    from agent.mind.execution.tool_execution import execute_one_tool

    service, _ = activity
    monkeypatch.setattr("agent.mind.execution.tool_execution.check_tool_permission", AsyncMock(return_value=None))
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
