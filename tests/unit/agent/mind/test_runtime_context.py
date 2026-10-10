"""动态执行事实的隔离、通知消费和缓存前缀纪律。"""

from copy import deepcopy
from unittest.mock import AsyncMock

from helpers.think_loop_fakes import FakeMind, run_think_loop, tool_result

from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.context_observation import emit_context_summary
from agent.mind.message_schema import normalize_for_send
from agent.mind.runtime_context import background_context
from agent.mind.tool_activation import bind_scope, reset_scope
from agent.mind.tools.think_loop import ThinkMode


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
    registry._waiting["user_qq:1"] = 1
    registry.complete(task, False, "读取失败，需要修正路径")
    assert background_context(registry, "user_qq:1") == ""
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
    monkeypatch.setattr("agent.mind.context_observation.event_bus.emit", emit)
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
    assert payload["blocks"][1]["label"] == "技能匹配与显式调用"
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
    from agent.mind.tools.think_loop import reply_entry

    done = AsyncMock()
    monkeypatch.setattr("agent.mind.tools.think_loop.reply_loop", AsyncMock(side_effect=RuntimeError("工具不可用")))
    monkeypatch.setattr("agent.mind.tools.think_loop.complete_reply", done)
    await reply_entry(FakeMind(), anything)
    deliver_mock.assert_not_awaited()
    assert done.await_args.kwargs["error"] is True
    assert "工具不可用" in done.await_args.args[2]
