"""动态执行事实的隔离、通知消费和缓存前缀纪律。"""

import asyncio
import inspect
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from helpers.think_loop_fakes import FakeMind, run_think_loop, tool_result

from agent.context.message_schema import normalize_for_send
from agent.context.observation import emit_context_summary, observe_preparation
from agent.mind.background.tasks import BackgroundTaskRegistry
from agent.mind.execution.state import ThinkMode
from agent.mind.runtime_context import background_context
from core.conversation_scope import bind_scope, reset_scope


async def test_preparation_observer_preserves_signature_messages_and_event_order(monkeypatch):
    events = []
    messages = [{"role": "system", "content": "稳定前缀", "_layer": "stable"},
                {"role": "user", "content": "原始消息", "_layer": "conversation"},
                {"role": "system", "content": "召回证据", "_layer": "memory"}]
    before = deepcopy(messages)
    wire = normalize_for_send(messages)
    marker = object()

    async def emit(event, payload):
        events.append(payload["status"])

    async def prepare(mind, conversation_list, anything, *, lean):
        assert mind is marker and conversation_list is messages and anything is None and lean
        events.append("prepare")
        return messages

    monkeypatch.setattr("agent.context.observation.event_bus.emit", emit)
    observed = observe_preparation(prepare)
    assert inspect.iscoroutinefunction(observed)
    assert inspect.signature(observed) == inspect.signature(prepare)
    assert await observed(marker, messages, None, lean=True) is messages
    assert events == ["running", "prepare", "done"]
    assert messages == before and normalize_for_send(messages) == wire


@pytest.mark.parametrize("error", [ValueError("invalid input"), asyncio.CancelledError("stop")])
async def test_preparation_failure_is_observed_without_replacing_exception(monkeypatch, error):
    emit = AsyncMock()
    monkeypatch.setattr("agent.context.observation.event_bus.emit", emit)

    @observe_preparation
    async def prepare():
        raise error

    with pytest.raises(type(error)) as caught:
        await prepare()
    assert caught.value is error
    assert [call.args[1]["status"] for call in emit.await_args_list] == [
        "running", "interrupted" if isinstance(error, asyncio.CancelledError) else "error"]


@pytest.mark.parametrize("running", [True, False])
async def test_reply_entry_applies_only_running_channel_recall_default(monkeypatch, running):
    from agent.channel.channel_types import ChannelStatus
    from agent.channel.reply_policy import ReplyPolicy
    from agent.memory.recall_policy import RecallPolicy, current_recall_policy, default_recall_policy, has_recall_turn
    from agent.messages.everything import Everything
    from agent.mind.execution.reply import reply_loop

    anything = Everything(adapter_key="game", uid=1, text_content="检查附近状态")
    scope = anything.entity_scope
    policy = RecallPolicy(level="off", background=False)
    channel = SimpleNamespace(status=ChannelStatus.RUNNING if running else ChannelStatus.STOPPED,
                              reply_policy=ReplyPolicy(recall_policy=policy))
    manager = SimpleNamespace(get=lambda key: channel if key == "game" else None)
    monkeypatch.setattr("agent.channel.manager.get_channel_manager", lambda: manager)
    expected = policy if running else default_recall_policy()
    async def collect(mind, **kwargs):
        assert current_recall_policy(scope) == expected
        return [{"role": "system", "content": "固定前缀"}]
    async def execute(*args, **kwargs):
        assert current_recall_policy(scope) == expected
        assert kwargs["base_messages"] == [{"role": "system", "content": "固定前缀"}]
    loop = AsyncMock(side_effect=execute)
    monkeypatch.setattr("agent.mind.execution.reply.think_loop", loop)
    monkeypatch.setattr("agent.mind.recall.coordinator.prepare_recollection", collect)
    mind = SimpleNamespace(config=SimpleNamespace(max_tool_iterations=5),
                           pfc=SimpleNamespace(consume_scope_task=Mock()),
                           tool_assembly=SimpleNamespace(get_active_tool_schemas=AsyncMock(return_value=[]),
                                                        clear_dynamic_tools=Mock()))
    await reply_loop(mind, anything, adapter_key="game")
    loop.assert_awaited_once()
    assert not has_recall_turn(scope)


def test_background_summary_is_bounded_and_scope_local():
    registry = BackgroundTaskRegistry()
    for i in range(10):
        registry.register("user_qq:1", "delegation", f"任务 {i} " + "长" * 1000)
    registry.register("user_telegram:1", "shell", "另一个频道的机密任务")
    text = background_context(registry, "user_qq:1")
    assert text.count("[task_id:") == 8
    assert "另有 2 项" in text
    assert "机密任务" not in text
    assert len(text) < 2500
    assert background_context(registry, "") == ""


async def test_observation_does_not_consume_completion():
    registry = BackgroundTaskRegistry()
    task = registry.register("user_qq:1", "delegation", "核对状态")
    background_context(registry, "user_qq:1")
    registry._wait_targets["user_qq:1"] = [""]
    registry.complete(task, False, "读取失败，需要修正路径")
    assert "读取失败" in background_context(registry, "user_qq:1")
    result = await registry.wait_any("user_qq:1", timeout=0)
    assert len(result.completions) == 1
    assert result.completions[0].success is False


async def test_task_updates_only_change_dynamic_tail(anything):
    mind = FakeMind(rounds=[tool_result("", ["recall"]), tool_result("", ["end_reply"])])
    mind.background_tasks = BackgroundTaskRegistry()
    mind.background_tasks.register("user_test:1", "delegation", "子任务仍在运行")
    base = [{"role": "system", "content": "稳定前缀", "_layer": "stable"},
            {"role": "user", "content": "历史内容", "_layer": "conversation"}]
    original = deepcopy(base)
    token = bind_scope("user_test:1")
    try:
        await run_think_loop(mind, anything=anything, base_messages=base)
    finally:
        reset_scope(token)
    assert base == original
    for messages in mind.sent_messages:
        assert messages[:len(base)] == original
        assert "子任务仍在运行" in messages[-1]["content"]
        assert all("子任务仍在运行" not in str(msg) for msg in messages[:-1])


async def test_context_observation_reports_actual_blocks_without_mutating_messages(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr("agent.context.observation.event_bus.emit", emit)
    messages = [{"role": "system", "_layer": "stable", "content": "稳定前缀"},
                {"role": "system", "_layer": "relation", "content": "[uid:42][topic:review] 关联证据"},
                {"role": "system", "_layer": "memory", "content": "[相关技能] review-check",
                 "_source": {"origin": "skill_match"}}]
    original = deepcopy(messages)
    await emit_context_summary(messages, 42)
    payload = emit.call_args.args[1]
    assert messages == original
    assert [block["layer"] for block in payload["blocks"]] == ["relation", "memory"]
    assert payload["duration_ms"] == 42
    assert payload["block_count"] == 2
    assert payload["blocks"][1]["label"] == "技能匹配"
    assert normalize_for_send(messages) == normalize_for_send([
        {key: value for key, value in msg.items() if key != "_source"} for msg in original
    ])


async def test_repeated_failures_do_not_report_success():
    mind = FakeMind(rounds=[tool_result("", ["recall"]) for _ in range(3)])
    mind.tool_results["recall"] = '{"error":"检索不可用","retryable":false}'
    completion = {}
    await run_think_loop(mind, mode=ThinkMode.REFLECT, completion=completion)
    assert completion["reason"] == "failed"


async def test_reply_exception_is_reported_without_automatic_channel_delivery(anything, deliver_mock, monkeypatch):
    from agent.mind.execution.reply import reply_entry

    done = AsyncMock()
    monkeypatch.setattr("agent.mind.execution.reply.reply_loop", AsyncMock(side_effect=RuntimeError("工具不可用")))
    monkeypatch.setattr("agent.mind.execution.reply.complete_reply", done)
    await reply_entry(FakeMind(), anything)
    deliver_mock.assert_not_awaited()
    assert done.await_args.kwargs["error"] is True
    assert "工具不可用" in done.await_args.args[2]


async def test_skill_review_evidence_only_counts_current_round(anything):
    from agent.skills.background_review import needs_review
    from core.event_bus import EVENT_AFTER_REPLY, event_bus
    events = []
    async def collect(payload):
        events.append(payload)
    event_bus.on(EVENT_AFTER_REPLY, collect, owner="test-review-evidence")
    history = [{"role": "assistant", "tool_calls": [
        {"id": f"old-{i}", "function": {"name": "read_file", "arguments": "{}"}} for i in range(5)
    ]}, *[{"role": "tool", "tool_call_id": f"old-{i}", "content": "ok"} for i in range(5)]]
    mind = FakeMind(rounds=[tool_result("", ["end_reply"])])
    try:
        await run_think_loop(mind, anything=anything, base_messages=history)
    finally:
        event_bus.off_by_owner("test-review-evidence")
    assert events
    assert all(item["name"] != "read_file" for item in events[-1]["executed_tools"])
    assert not needs_review(events[-1])


def test_old_hook_results_leave_dynamic_tail_but_remain_queryable(monkeypatch):
    registry = BackgroundTaskRegistry()
    task_id = registry.register("user_qq:1", "llm_hook", "review")
    registry.complete(task_id, False, "old failure")
    finished = registry.completed("user_qq:1")[0].finished_at
    monkeypatch.setattr("agent.mind.runtime_context.time.time", lambda: finished + 301)
    assert background_context(registry, "user_qq:1") == ""
    assert registry.read_task_output("user_qq:1", task_id)["summary"] == "old failure"
