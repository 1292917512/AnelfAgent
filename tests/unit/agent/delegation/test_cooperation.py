"""委派等待、软超时、进度与主动停止的协作契约。"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.delegation import journal
from agent.delegation.delegate_tool import delegate_task, delegation_manager_port, follow_up_agent
from agent.delegation.delegation_manager import DelegationManager
from agent.delegation.dispatch import dispatch_delegations
from agent.mind.background.tasks import BackgroundTaskRegistry
from agent.mind.interrupt import InterruptRegistry
from agent.mind.scope_usage import bind_usage_scope, reset_usage_scope
from core.event_bus import EVENT_THINKING_TOOL_END, EVENT_THINKING_TOOL_START, event_bus

SCOPE = "user_webui:web_user#cooperation"


@pytest.fixture
async def harness(monkeypatch):
    registry = BackgroundTaskRegistry(loop=asyncio.get_running_loop())
    unclaimed, alert = AsyncMock(), AsyncMock()
    registry.set_unclaimed_callback(unclaimed)
    registry.set_alert_callback(alert)
    started, release, stopping, drained = (asyncio.Event() for _ in range(4))
    drained.set()
    mind = SimpleNamespace(
        background_tasks=registry, interrupts=InterruptRegistry(),
        models=SimpleNamespace(llm_manager=None), model_invoker=SimpleNamespace(get_context_length=lambda: 128000),
        pfc=SimpleNamespace(message_revision=0, get_adapter_key=lambda scope: "webui",
                            append_notification=AsyncMock(return_value=True)),
    )
    messages_seen = []

    async def reflect(messages, *, completion, **kwargs):
        messages_seen.append(messages)
        run_id, scope = uuid.uuid4().hex, "reflect:" + uuid.uuid4().hex
        completion["turn_id"] = run_id
        with mind.interrupts.execution(run_id, scope):
            await event_bus.emit(EVENT_THINKING_TOOL_START, {"tool_id": "op", "tool_name": "probe", "arguments": "{}"})
            started.set()
            waiters = {asyncio.create_task(release.wait()), asyncio.create_task(mind.interrupts.event(scope).wait())}
            try:
                await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
                cancelled = mind.interrupts.is_requested(scope)
                if cancelled:
                    stopping.set()
                    await drained.wait()
                await event_bus.emit(EVENT_THINKING_TOOL_END, {
                    "tool_id": "op", "tool_name": "probe", "result": "partial receipt", "success": not cancelled,
                    "cancelled": cancelled,
                })
                completion.update(reason="interrupted" if cancelled else "completed", messages=[
                    *messages, {"role": "assistant", "content": "已有操作回执 partial receipt"},
                ])
                return "已停止" if cancelled else "完成结果"
            finally:
                for waiter in waiters:
                    waiter.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)

    mind.reflect = reflect
    manager = DelegationManager(mind)
    delegation_manager_port.set(manager)
    token = bind_usage_scope(SCOPE)
    monkeypatch.setattr("agent.delegation.dispatch.FOREGROUND_WAIT_SECONDS", 0.03)
    state = SimpleNamespace(manager=manager, mind=mind, registry=registry, unclaimed=unclaimed, alert=alert,
                            started=started, release=release, stopping=stopping, drained=drained, seen=messages_seen)
    try:
        yield state
    finally:
        release.set()
        drained.set()
        await manager.close()
        await registry.close()
        reset_usage_scope(token)


async def test_execution_timeout_only_alerts_and_wakes_waiter_with_shared_progress(harness, monkeypatch):
    h = harness
    monkeypatch.setattr("agent.delegation.delegation_manager.delegation_timeout_seconds", lambda: 0.03)
    monkeypatch.setattr("agent.delegation.dispatch.FOREGROUND_WAIT_SECONDS", 2)
    payload = json.loads(await delegate_task(goal="慢任务"))
    did = payload["delegation_id"]
    task = h.manager._background_tasks[did]
    assert payload["reason"] == "alert" and payload["pending_ids"] == [did]
    assert not task.done() and not h.stopping.is_set()
    await asyncio.sleep(0)
    h.alert.assert_awaited_once()
    await asyncio.sleep(0.07)
    h.alert.assert_awaited_once()
    ai = h.registry.read_task_output(SCOPE, did)
    ui = h.manager.running_snapshot(SCOPE)[0]
    assert not ai["done"] and ai["progress"]["overdue"] and ui["overdue"]
    assert ai["progress"]["current_tool"] == ui["current_tool"] == "probe"
    assert ai["evidence"] and ai["alert"]
    assert not h.registry.read_task_output(SCOPE, did)["delta"]  # 进度快照不依赖日志消费游标
    h.release.set()
    result = await task
    assert result.success and journal.load_transcript(did)
    await asyncio.sleep(0)
    h.unclaimed.assert_awaited_once()
    assert h.registry.progress(did)["phase"] == "completed"


@pytest.mark.parametrize("background", [False, True])
async def test_batch_foreground_wait_and_background_return_ids_without_cancelling(harness, background):
    h = harness
    payload = json.loads(await delegate_task(tasks='[{"goal":"A"},{"goal":"B"}]', background=background))
    ids = payload["delegation_ids"]
    tasks = [h.manager._background_tasks[did] for did in ids]
    assert len(ids) == 2 and all(not task.done() for task in tasks)
    if not background:
        assert payload["reason"] == "timeout" and not payload["completion_ready"]
    h.release.set()
    results = await asyncio.gather(*tasks)
    await asyncio.sleep(0)
    assert [result.task_index for result in results] == [0, 1]
    assert h.unclaimed.await_count == 2


async def test_outer_tool_timeout_does_not_own_child_lifetime(harness):
    from core.entity import EntityRegistry
    from core.tool_registry import tool

    h = harness
    tool(name="cooperation_delegate")(delegate_task)
    try:
        payload = json.loads(await EntityRegistry.execute_tool("cooperation_delegate", '{"goal":"长任务"}', timeout=0.01))
        assert payload["code"] == "TOOL_TIMEOUT"
        did = h.registry.running(SCOPE)[0].task_id
        task = h.manager._background_tasks[did]
        assert not task.done()
        h.release.set()
        assert (await task).success
        await asyncio.sleep(0)
        h.unclaimed.assert_awaited_once()
    finally:
        EntityRegistry.unregister("cooperation_delegate")


async def test_new_channel_message_interrupts_delegate_wait_without_cancelling_or_changing_owner(harness, monkeypatch):
    from agent.messages.presets import MessageUser
    from agent.mind.prefrontal_cortex import PrefrontalCortex

    h = harness
    entity = SimpleNamespace(add_conversations_num=lambda: 0, personality={}, uid=0)
    h.mind.pfc = PrefrontalCortex(everything_data=SimpleNamespace(get_anything=AsyncMock(return_value=entity)))
    monkeypatch.setattr("agent.delegation.dispatch.FOREGROUND_WAIT_SECONDS", 5)
    monkeypatch.setattr("agent.mind.background.tasks._POLL_INTERVAL", 0.01)
    waiter = asyncio.create_task(delegate_task(goal="等待期间接收其他频道消息"))
    await h.started.wait()
    incoming = MessageUser(uid="2", adapter_key="qq", text_content="另一频道有最新要求")
    await h.mind.pfc.add_task(incoming)
    payload = json.loads(await asyncio.wait_for(waiter, 1))
    did = payload["delegation_id"]
    task = h.manager._background_tasks[did]
    assert payload["reason"] == "interrupted" and payload["pending_ids"] == [did]
    assert not task.done() and not h.stopping.is_set()
    assert h.registry.running(SCOPE)[0].task_id == did
    assert not h.registry.read_task_output(incoming.entity_scope, did)["ok"]
    assert h.mind.pfc.peek_all_tasks()[0][0] == incoming.entity_scope
    assert "另一频道有最新要求" not in json.dumps(h.seen, ensure_ascii=False)
    token = bind_usage_scope(incoming.entity_scope)
    try:
        h.release.set()
        assert (await task).success
        await asyncio.sleep(0)
        h.unclaimed.assert_awaited_once()
        assert h.unclaimed.await_args.args[0] == SCOPE
        assert journal.load_transcript(did)["scope"] == SCOPE
    finally:
        reset_usage_scope(token)


@pytest.mark.parametrize("source", ["ai", "user"])
async def test_explicit_stop_waits_for_drain_preserves_transcript_and_follow_up(harness, source):
    h = harness
    h.drained.clear()
    did = h.manager.delegate_background("保留执行记录", scope=SCOPE)
    task = h.manager._background_tasks[did]
    await h.started.wait()
    if source == "ai":
        accepted = await asyncio.to_thread(h.registry.terminate, SCOPE, did)
        assert accepted["ok"]
    else:
        assert h.manager.cancel(did)
    await asyncio.wait_for(h.stopping.wait(), 2)
    assert not task.done() and h.registry.running(SCOPE)
    assert h.manager.running_snapshot(SCOPE)[0]["cancel_requested"]
    assert h.registry.read_task_output(SCOPE, did)["progress"]["phase"] == "stopping"
    h.drained.set()
    result = await task
    assert result.cancelled and result.cancel_source == source and result.completed_reason == "interrupted"
    transcript = journal.load_transcript(did)
    assert transcript["cancel_source"] == source and "partial receipt" in json.dumps(transcript)
    assert any(item["delegation_id"] == did and item["status"] == "cancelled" for item in journal.recent_history(50))
    h.release.set()
    resumed = json.loads(await follow_up_agent(did, "基于已有回执继续"))
    resumed_id = resumed["delegation_id"]
    assert resumed_id != did
    if pending := h.manager._background_tasks.get(resumed_id):
        assert (await pending).success
    assert journal.load_transcript(resumed_id)["success"]
    assert "partial receipt" in json.dumps(h.seen[-1])
    assert h.seen[-1][-1]["content"].endswith("基于已有回执继续")


async def test_foreground_completion_has_result_before_notification_storage_finishes(harness, monkeypatch):
    h = harness
    h.release.set()
    writing = asyncio.Event()

    async def store(*args):
        await writing.wait()
        return True

    h.mind.pfc.append_notification = store
    monkeypatch.setattr("agent.delegation.dispatch.FOREGROUND_WAIT_SECONDS", 1)
    try:
        payload = await dispatch_delegations(h.manager, [{"goal": "快速结果"}])
        assert payload["completion_ready"] and payload["results"][0]["output"] == "完成结果"
        assert not payload["pending_ids"]
        h.unclaimed.assert_not_called()
    finally:
        writing.set()


async def test_shutdown_before_background_start_closes_record_and_retains_resume(harness):
    h = harness
    did = h.manager.delegate_background("尚未开始", scope=SCOPE)
    await h.manager.close()
    assert not h.registry.running(SCOPE)
    assert journal.load_transcript(did)
    assert not h.manager._progress and not h.manager._background_tasks


async def test_explicit_caller_deadline_still_stops_execution_and_preserves_receipts(harness):
    h = harness
    deadline = asyncio.timeout(None)

    async def call():
        async with deadline:
            await h.manager.delegate("工作流显式截止", scope_hint=SCOPE)

    caller = asyncio.create_task(call())
    try:
        await h.started.wait()
        deadline.reschedule(asyncio.get_running_loop().time())
        with pytest.raises(TimeoutError):
            await caller
    finally:
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
    assert h.stopping.is_set() and not h.manager._running
    result = h.registry.completed(SCOPE)[0]
    transcript = journal.load_transcript(result.task_id)
    assert transcript["completed_reason"] == "interrupted"
    assert "partial receipt" in json.dumps(transcript)


@pytest.mark.parametrize("partial_output", [True, False])
async def test_runtime_signal_records_cancellation_with_or_without_partial_output(harness, partial_output):
    h = harness
    reflect = h.mind.reflect

    async def interrupted_reflect(*args, **kwargs):
        output = await reflect(*args, **kwargs)
        return output if partial_output else ""

    h.mind.reflect = interrupted_reflect
    did = h.manager.delegate_background("运行时停止", scope=SCOPE)
    task = h.manager._background_tasks[did]
    await h.started.wait()
    assert h.manager._running[did]["agent_instance"].request_stop("服务关停")
    result = await task
    assert result.cancelled and not result.success and result.cancel_source == "runtime"
    assert h.registry.progress(did)["phase"] == "cancelled"
    transcript = journal.load_transcript(did)
    assert transcript["cancelled"] and "partial receipt" in json.dumps(transcript)
    assert any(item["delegation_id"] == did and item["status"] == "cancelled" for item in journal.recent_history(50))


async def test_queued_tasks_do_not_start_execution_timer_and_can_be_stopped(harness, monkeypatch):
    h = harness
    monkeypatch.setattr("agent.delegation.delegation_manager.delegation_timeout_seconds", lambda: 0.02)
    h.manager._semaphore = asyncio.Semaphore(0)
    did = h.manager.delegate_background("排队取消", scope=SCOPE)
    task = h.manager._background_tasks[did]
    await asyncio.sleep(0.06)
    assert h.manager.running_snapshot(SCOPE)[0]["phase"] == "queued"
    h.alert.assert_not_called()
    assert (await asyncio.to_thread(h.registry.terminate, SCOPE, did))["ok"]
    result = await task
    assert result.cancelled and result.cancel_source == "ai"
    assert not h.started.is_set() and journal.load_transcript(did)
    assert any(item["delegation_id"] == did and item["status"] == "cancelled" for item in journal.recent_history(50))


async def test_parent_stop_includes_child_admitted_before_child_coroutine_starts(harness):
    from agent.delegation.sub_agent import bind_delegation_id, reset_delegation_id

    h = harness
    parent = h.manager.delegate_background("父任务", scope=SCOPE)
    parent_task = h.manager._background_tasks[parent]
    await h.started.wait()
    token = bind_delegation_id(parent)
    try:
        child = h.manager.delegate_background("子任务", scope=SCOPE)
    finally:
        reset_delegation_id(token)
    child_task = h.manager._background_tasks[child]
    assert h.manager.cancel(parent)
    results = await asyncio.gather(parent_task, child_task)
    assert all(result.cancelled for result in results)
    assert len(h.seen) == 1
    assert not h.manager._children and not h.manager._pending
