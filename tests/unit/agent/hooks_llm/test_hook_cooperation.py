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
from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.runtime_context import background_context
from core.activity import activity_scope
from core.event_bus import EVENT_THINKING_LLM_START, EVENT_THINKING_TOOL_START, event_bus
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
    from agent.llm.prompt_cache import decorate_messages
    from agent.mind.message_schema import normalize_for_send
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


@pytest.mark.parametrize("mode, expected", [(HookContextMode.NONE, []), (HookContextMode.LEAN, [{"role": "system", "content": "fixed instructions"}])])
async def test_context_choice_is_applied_before_handler(mode, expected):
    seen = []
    async def handler(ctx):
        seen.append(ctx.messages)
        assert ctx.inherited_tools is None
    mind = SimpleNamespace(background_tasks=None, get_recollection=AsyncMock(return_value=[{"role": "system", "content": "fixed instructions"}]))
    executor = HookExecutor(mind)
    await executor.dispatch("after_reply", [LLMHookSpec("context", "after_reply", handler, context=mode, mode=HookMode.SYNC)], {"messages": [{"role": "user", "content": "private transcript"}], "tools": []})
    assert seen == [expected]
    assert mind.get_recollection.await_count == (1 if mode is HookContextMode.LEAN else 0)
