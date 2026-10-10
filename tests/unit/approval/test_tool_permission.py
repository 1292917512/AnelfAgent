"""执行入口的中断、失败原因与任务内反馈。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent.approval.types import ApprovalResult
from agent.mind.interrupt import InterruptRegistry
from agent.mind.tools.permission import check_tool_permission


@pytest.fixture()
def gate(monkeypatch):
    gate = SimpleNamespace(check=AsyncMock(return_value=ApprovalResult(True, "rule_allow")))
    monkeypatch.setattr("agent.approval.get_approval_gate", lambda: gate)
    monkeypatch.setattr("agent.hooks.hooks_active", lambda _: False)
    return gate


async def test_denial_reason_reaches_model(gate):
    gate.check.return_value = ApprovalResult(False, "guardian_denied", "不可逆删除系统目录")
    result = json.loads(await check_tool_permission(SimpleNamespace(name="delete_file", id="1", arguments="{}"), None, "user_qq:123"))
    assert result["error"] == "不可逆删除系统目录"
    assert result["permission_outcome"] == "guardian_denied" and not result["retryable"]


async def test_bypass_notice_is_task_local(gate):
    gate.check.return_value = ApprovalResult(True, "guardian_bypass", "timeout", "评审超时")
    mind = SimpleNamespace(interrupts=InterruptRegistry(), push_hub=SimpleNamespace(push=Mock()))
    tc = SimpleNamespace(name="t", id="1", arguments="{}")
    assert await check_tool_permission(tc, None, "user_webui:u#permission-test", mind) is None
    mind.push_hub.push.assert_called_once_with("user_webui:u#permission-test", "approval", "评审超时", trigger=False)


async def test_interrupt_during_review_prevents_execution(gate):
    interrupts = InterruptRegistry()
    async def review(**kwargs):
        interrupts.request("user_qq:123", "stop")
        return ApprovalResult(True, "guardian_approved")
    gate.check.side_effect = review
    result = await check_tool_permission(SimpleNamespace(name="t", id="1", arguments="{}"), None, "user_qq:123", SimpleNamespace(interrupts=interrupts))
    assert json.loads(result)["permission_outcome"] == "cancelled"


async def test_cancelled_model_request_propagates(gate):
    gate.check.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await check_tool_permission(SimpleNamespace(name="t", id="1", arguments="{}"), None, "reflect")


@pytest.mark.parametrize("arguments", ["[]", "null", "broken JSON"])
async def test_malformed_arguments_do_not_skip_permissions(gate, arguments):
    result = await check_tool_permission(SimpleNamespace(name="t", id="1", arguments=arguments), None, "reflect")
    assert json.loads(result)["permission_outcome"] == "invalid_arguments"
    gate.check.assert_not_awaited()
