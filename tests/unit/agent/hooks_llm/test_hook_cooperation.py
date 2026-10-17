"""钩子的完整上下文、协作可见性、取消与配置一致性。"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.hooks_llm import HookExecutor, HookRegistry, LLMHookSpec
from agent.hooks_llm.settings import config_keys
from agent.hooks_llm.spec import HookContextMode
from agent.mind.background.tasks import BackgroundTaskRegistry
from agent.mind.runtime_context import background_context
from core.activity import activity_scope
from core.event_bus import (
    EVENT_THINKING_LLM_END,
    EVENT_THINKING_LLM_START,
    EVENT_THINKING_TOOL_END,
    EVENT_THINKING_TOOL_START,
    event_bus,
)
from core.hooks import HookMode, hook_journal
from services.workspace_activity import WorkspaceActivityService


@pytest.fixture(autouse=True)
def clean():
    hook_journal.clear()
    HookRegistry.clear()
    yield
    HookRegistry.clear()
    hook_journal.clear()


async def test_running_hook_is_inspectable_cancellable_and_scope_local():
    registry = BackgroundTaskRegistry()
    ready = asyncio.Event()
    projection = WorkspaceActivityService()
    projection.start()

    async def handler(ctx):
        async with activity_scope("review-child", "reflect:review"):
            await event_bus.emit(EVENT_THINKING_LLM_START, {"model": "review-model"})
            await event_bus.emit(EVENT_THINKING_TOOL_START, {"tool_id": "read-1", "tool_name": "get_skill"})
            await event_bus.emit(EVENT_THINKING_LLM_END, {"model": "review-model"})
            ready.set()
            await asyncio.Event().wait()

    executor = HookExecutor(SimpleNamespace(background_tasks=registry))
    try:
        async with activity_scope("parent", "user_qq:1"):
            await executor.dispatch("after_reply", [LLMHookSpec("skill_review", "after_reply", handler, route_output=False)], {"scope": "user_qq:1"})
            await ready.wait()
        task = registry.running("user_qq:1")[0]
        progress = registry.read_task_output("user_qq:1", task.task_id)
        assert progress["progress"]["phase"] == "tool"
        assert progress["progress"]["operation"] == "get_skill"
        assert progress["progress"]["model_calls"] == 1
        assert progress["progress"]["activity_ids"] == ["review-child"]
        assert progress["evidence"]["events"][0]["activity_id"] == "review-child"
        assert "get_skill" in background_context(registry, "user_qq:1")
        assert background_context(registry, "user_webui:1") == ""
        assert not registry.terminate("user_webui:1", task.task_id)["ok"]
        assert not registry.read_task_output("user_webui:1", task.task_id)["ok"]
        runs = projection.snapshot()["runs"]
        hook = next(entry for entry in runs[0]["entries"] if entry["kind"] == "hook")
        assert hook["activity_ids"] == ["review-child"]
        assert runs[1]["kind"] == "hook" and runs[1]["owner_id"] == hook["id"]
        assert (await asyncio.to_thread(registry.terminate, "user_qq:1", task.task_id))["ok"]
        await executor.drain()
        assert not registry.running("user_qq:1")
        assert registry.completed("user_qq:1")[0].success is False
        assert "钩子已取消" in background_context(registry, "user_qq:1")
        assert (await registry.wait_any("user_qq:1", timeout=0)).completions == []
        assert hook_journal.snapshot()["recent"][0]["status"] == "cancelled"
        assert registry._records[task.task_id].killer is None
        assert registry._records[task.task_id].progress_reader is None
    finally:
        await executor.drain(timeout=0)
        projection.stop()


async def test_no_hook_lifetime_deadline_and_queued_hook_does_not_starve_other_hooks(monkeypatch):
    def no_deadline(*args, **kwargs):
        raise AssertionError("LLM hook must not impose a wall-clock deadline")
    monkeypatch.setattr("agent.hooks_llm.executor.asyncio.timeout", no_deadline)
    first, release, independent = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def busy(ctx):
        first.set()
        await release.wait()

    async def other(ctx):
        independent.set()

    registry = BackgroundTaskRegistry()
    executor = HookExecutor(SimpleNamespace(background_tasks=registry), pool_size=2)
    try:
        spec = LLMHookSpec("busy", "after_reply", busy)
        await executor.dispatch("after_reply", [spec], {"scope": "user_qq:1"})
        await first.wait()
        await executor.dispatch("after_reply", [spec], {"scope": "user_qq:2"})
        await executor.dispatch("after_reply", [LLMHookSpec("other", "after_reply", other)], {})
        await asyncio.wait_for(independent.wait(), timeout=1)
        assert len(registry.running("user_qq:1")) == 1
        assert registry.snapshot("user_qq:2")["running"][0]["progress"]["phase"] == "queued"
        release.set()
        await executor.drain()
        assert all(run["status"] == "done" for run in hook_journal.snapshot()["recent"])
    finally:
        await executor.drain(timeout=0)


async def test_complete_transcript_keeps_tools_nested_content_and_cache_layers():
    ready, release = asyncio.Event(), asyncio.Event()
    messages = [{"role": "system", "content": "stable" * 15000, "_layer": "stable"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_skill", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "full evidence"}]
    tools = [{"type": "function", "function": {"name": "get_skill", "parameters": {"type": "object"}}}]
    original, original_tools = deepcopy(messages), deepcopy(tools)
    seen = []

    async def handler(ctx):
        ready.set()
        await release.wait()
        seen.append((ctx.messages, ctx.inherited_tools))

    executor = HookExecutor(SimpleNamespace(background_tasks=None))
    await executor.dispatch("after_reply", [LLMHookSpec("review", "after_reply", handler, context=HookContextMode.TRANSCRIPT)], {"messages": messages, "tools": tools})
    await ready.wait()
    messages[1]["tool_calls"][0]["function"]["name"] = "mutated"
    tools[0]["function"]["name"] = "mutated"
    release.set()
    await executor.drain()
    assert seen == [(original, original_tools)]
    from agent.context.message_schema import normalize_for_send
    from agent.llm.prompt_cache import decorate_messages
    decorated = decorate_messages(seen[0][0], anthropic=True)
    assert "cache_control" in str(decorated[0])
    assert all("_layer" not in item for item in normalize_for_send(decorated))


async def test_budget_exhaustion_is_not_reported_as_success():
    registry = BackgroundTaskRegistry()
    async def handler(ctx):
        async with activity_scope("budget-run", "reflect:test") as activity:
            activity.status = "budget_exhausted"
        return "still checking"
    executor = HookExecutor(SimpleNamespace(background_tasks=registry))
    await executor.dispatch("after_reply", [LLMHookSpec("review", "after_reply", handler, mode=HookMode.SYNC, route_output=False)], {"scope": "user_qq:1"})
    assert registry.completed("user_qq:1")[0].success is False
    record = hook_journal.snapshot()["recent"][0]
    assert record["status"] == "error" and "budget_exhausted" in record["detail"]


async def test_hook_evidence_is_scoped_sanitized_and_cursor_readable():
    registry = BackgroundTaskRegistry()

    async def handler(ctx):
        await event_bus.emit(EVENT_THINKING_TOOL_START, {
            "tool_id": "read", "tool_name": "get_skill", "arguments": '{"password":"private-value"}',
        })
        await event_bus.emit(EVENT_THINKING_TOOL_END, {
            "tool_id": "read", "tool_name": "get_skill", "result": "x" * 5000,
            "error": "missing skill", "success": False, "duration_ms": 12,
        })

    executor = HookExecutor(SimpleNamespace(background_tasks=registry))
    await executor.dispatch("after_reply", [LLMHookSpec("review", "after_reply", handler, mode=HookMode.SYNC)], {"scope": "user_qq:1"})
    task_id = registry.completed("user_qq:1")[0].task_id
    result = registry.read_task_output("user_qq:1", task_id)
    events = result["evidence"]["events"]
    assert len(events) == 2
    assert "private-value" not in str(events)
    assert events[1]["error"] == "missing skill" and events[1]["duration_ms"] == 12
    assert events[1]["truncated"] is True
    assert not registry.read_task_output("user_webui:1", task_id)["ok"]
    assert registry.read_task_output("user_qq:1", task_id, after_sequence=result["evidence"]["next_sequence"])["evidence"]["events"] == []


async def test_stall_alert_is_coalesced_does_not_cancel_and_stops_on_completion():
    import time
    registry = BackgroundTaskRegistry()
    alerted = asyncio.Event()
    alerts = []
    def alert(*args):
        alerts.append(args)
        alerted.set()
    registry.set_alert_callback(alert)
    task_id = registry.register("user_qq:1", "llm_hook", "review")
    progress = {"phase": "queued", "updated_at": time.time() - 10}
    registry.attach_progress(task_id, lambda: progress)
    registry.watch_stall(task_id, .02)
    try:
        await asyncio.wait_for(alerted.wait(), 1)
        assert len(registry.running("user_qq:1")) == 1
        await asyncio.sleep(.06)
        assert len(alerts) == 1
        alerted.clear()
        progress["updated_at"] = time.time() - 1
        await asyncio.wait_for(alerted.wait(), 1)
        assert len(alerts) == 2
    finally:
        registry.complete(task_id, True, "done")
    assert registry._records[task_id].stall_timer is None


def test_evidence_eviction_is_explicit_and_reads_do_not_steal_other_readers():
    registry = BackgroundTaskRegistry()
    task_id = registry.register("user_qq:1", "llm_hook", "review")
    for index in range(40):
        registry.record_evidence(task_id, "tool_end", result=str(index))
    first = registry.read_task_output("user_qq:1", task_id)["evidence"]
    assert first["history_evicted"] and len(first["events"]) == 32
    assert registry.read_task_output("user_qq:1", task_id)["evidence"] == first


def test_registered_config_is_live_persistent_and_shared_with_web():
    from agent.config import save_config_value
    from core.config import ConfigManager, ConfigRegistry
    from services.hooks_llm import hooks_llm_service
    async def handler(ctx):
        return None
    spec = LLMHookSpec("review", "after_reply", handler)
    HookRegistry.register(spec)
    keys = config_keys(spec)
    save_config_value(keys["context"], "transcript")
    save_config_value(keys["model"], "small-model")
    assert HookRegistry.get("review").context is HookContextMode.TRANSCRIPT
    assert HookRegistry.get("review").model == "small-model"
    assert hooks_llm_service.get_overview()["hooks"][0]["model"] == "small-model"
    assert HookRegistry.set_enabled("review", False)
    assert ConfigManager.get(keys["enabled"]) is False
    assert not HookRegistry.get("review").enabled
    HookRegistry.unregister("review")
    assert not ConfigRegistry.get_group_items("hooks_llm/review")
    HookRegistry.register(spec)
    assert not HookRegistry.get("review").enabled


def test_entity_sdk_exposes_stall_policy_through_shared_configuration():
    from entities._sdk import register_entity_llm_hook
    from services.hooks_llm import hooks_llm_service

    async def handler(ctx):
        return None

    assert register_entity_llm_hook("entity_review", "after_reply", handler, stall_seconds=45)
    hook = hooks_llm_service.get_overview()["hooks"][0]
    assert hook["stall_seconds"] == 45
    assert "stall_seconds" in hook["config_keys"]


@pytest.mark.parametrize("mode, expected", [(HookContextMode.NONE, []), (HookContextMode.LEAN, [{"role": "system", "content": "fixed instructions"}])])
async def test_context_choice_is_applied_before_handler(mode, expected, monkeypatch):
    seen = []
    async def handler(ctx):
        seen.append(ctx.messages)
        assert ctx.inherited_tools is None
    prepare = AsyncMock(return_value=[{"role": "system", "content": "fixed instructions"}])
    monkeypatch.setattr("agent.mind.recall.coordinator.prepare_recollection", prepare)
    mind = SimpleNamespace(background_tasks=None)
    executor = HookExecutor(mind)
    await executor.dispatch("after_reply", [LLMHookSpec("context", "after_reply", handler, context=mode, mode=HookMode.SYNC)], {"messages": [{"role": "user", "content": "private transcript"}], "tools": []})
    assert seen == [expected]
    assert prepare.await_count == (1 if mode is HookContextMode.LEAN else 0)
