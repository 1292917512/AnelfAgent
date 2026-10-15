"""Web 轮次控制的隔离、幂等与安全续接。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent.mind.background_tasks import BackgroundTaskRegistry
from agent.mind.interrupt import InterruptRegistry
from services import execution_control as control
from services.chat import ChatService
from services.workspace_activity import WorkspaceActivityService


@pytest.fixture
def execution(monkeypatch):
    projection = WorkspaceActivityService()
    monkeypatch.setattr("services.workspace_activity.publish", lambda *a, **kw: None)
    monkeypatch.setattr(control, "workspace_activity", projection)
    interrupts = InterruptRegistry()
    scope = ChatService.scope_for_chat("test")
    mind = SimpleNamespace(
        active_reply_scopes=set(), interrupts=interrupts, background_tasks=BackgroundTaskRegistry(),
        pfc=SimpleNamespace(peek_all_tasks=lambda: []), delegation_manager=None,
    )

    def interrupt(target, reason=""):
        if target not in mind.active_reply_scopes:
            return False
        interrupts.request(target, reason)
        return True

    mind.interrupt = interrupt
    runtime = SimpleNamespace(mind=mind)
    monkeypatch.setattr(control, "get_runtime", lambda: runtime)
    monkeypatch.setattr("services.chat.get_runtime", lambda: runtime)
    send = AsyncMock()
    monkeypatch.setattr(ChatService, "send_message", send)
    yield projection, mind, scope, send
    projection.stop()


async def start(projection, scope, run_id="run"):
    await projection._start({"turn_id": run_id, "scope": scope, "kind": "conversation"})


async def test_stop_is_scoped_and_does_not_report_completion_early(execution):
    projection, mind, scope, send = execution
    await start(projection, scope)
    mind.active_reply_scopes.update({scope, ChatService.scope_for_chat("other")})
    mind.delegation_manager = SimpleNamespace(cancel_scope=Mock(return_value=1))
    result = await control.control_execution("run", "stop")
    assert result["interrupted"] is True
    assert mind.interrupts.pending_scopes() == [scope]
    assert projection.get_run("run")["status"] == "running"
    assert projection.get_run("run")["control"] == "stopping"
    assert await control.control_execution("run", "stop") == {"status": "accepted"}
    mind.delegation_manager.cancel_scope.assert_called_once_with(scope)
    send.assert_not_called()


@pytest.mark.parametrize("case", ["other_channel", "old_turn", "not_stopped", "active", "queued", "background"])
async def test_invalid_resume_cannot_start_or_interrupt_work(execution, case):
    projection, mind, scope, send = execution
    await start(projection, "user_qq:123" if case == "other_channel" else scope)
    if case != "not_stopped":
        await projection._finish({"turn_id": "run", "status": "cancelled"})
    if case == "old_turn":
        await start(projection, scope, "new")
    if case == "active":
        mind.active_reply_scopes.add(scope)
    if case == "queued":
        mind.pfc.peek_all_tasks = lambda: [(scope, "", "", "")]
    if case == "background":
        mind.background_tasks.register(scope, "shell", "finishing cancellation")
    with pytest.raises(control.ExecutionControlError):
        await control.control_execution("run", "resume")
    send.assert_not_called()
    assert mind.interrupts.pending_scopes() == []


async def test_resume_is_once_and_uses_original_web_conversation(execution):
    projection, mind, scope, send = execution
    await start(projection, scope)
    await projection._finish({"turn_id": "run", "status": "cancelled"})
    entered, release = asyncio.Event(), asyncio.Event()

    async def accept(*a, **kw):
        entered.set()
        await release.wait()

    send.side_effect = accept
    first = asyncio.create_task(control.control_execution("run", "resume"))
    await entered.wait()
    assert await control.control_execution("run", "resume") == {"status": "accepted"}
    release.set()
    await first
    await start(projection, scope, "new")
    assert await control.control_execution("run", "resume") == {"status": "accepted"}
    send.assert_awaited_once()
    assert send.call_args.kwargs == {"chat_id": "test", "message_id": "resume-run"}
    assert "已完成的操作不要重复执行" in send.call_args.args[0]
    assert projection.get_run("run")["status"] == "cancelled"


async def test_old_stop_never_stops_new_turn(execution):
    projection, mind, scope, _ = execution
    await start(projection, scope)
    await start(projection, scope, "new")
    mind.active_reply_scopes.add(scope)
    with pytest.raises(control.ExecutionControlError):
        await control.control_execution("run", "stop")
    assert not mind.interrupts.is_requested(scope)
