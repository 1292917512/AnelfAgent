"""统一权限入口：所有调用来源共享裁决，不产生人工批准状态。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from agent.approval import ApprovalGate, PermissionEffect, PermissionRule, PermissionRuleSet
from agent.approval.guardian import GuardianVerdict
from agent.approval.redaction import redact_arguments


@pytest.mark.parametrize("scope", ["user_qq:123", "user_telegram:123", "user_webui:u#chat", "reflect"])
@pytest.mark.parametrize("approved,outcome", [(True, "guardian_approved"), (False, "guardian_denied"), (None, "guardian_bypass")])
async def test_same_decision_for_every_source(monkeypatch, fake_audit_sink, scope, approved, outcome):
    guardian = AsyncMock()
    guardian.review.return_value = GuardianVerdict(approved, "high", "评审原因")
    monkeypatch.setattr("agent.approval.gate.get_approval_guardian", lambda: guardian)
    gate = ApprovalGate(PermissionRuleSet(rules=[PermissionRule(pattern="write_file", effect="ask")]))
    result = await gate.check(tool_name="write_file", tool_args={}, reason="task", scope=scope)
    assert result.allowed is (approved is not False)
    assert result.outcome == outcome
    assert result.reason == "评审原因"
    assert bool(result.notice) is (approved is not False)
    assert fake_audit_sink.rows[-1]["outcome"] == outcome
    assert not hasattr(gate, "approve") and not hasattr(gate, "_manager")


async def test_channel_scope_is_enforced_without_channel_adapter(monkeypatch, fake_audit_sink):
    guardian = AsyncMock()
    monkeypatch.setattr("agent.approval.gate.get_approval_guardian", lambda: guardian)
    gate = ApprovalGate(PermissionRuleSet(rules=[PermissionRule(pattern="write_file", effect="deny", scope="qq")]))
    denied = await gate.check(tool_name="write_file", tool_args={}, reason="", scope="user_qq:123")
    allowed = await gate.check(tool_name="write_file", tool_args={}, reason="", scope="user_webui:123#chat")
    assert not denied.allowed and allowed.allowed
    guardian.review.assert_not_awaited()
    assert fake_audit_sink.rows[0]["channel_id"] == "qq"
    assert fake_audit_sink.rows[0]["user_id"] == "123"


async def test_rule_fault_never_bypasses_deny(monkeypatch, fake_audit_sink):
    gate = ApprovalGate(PermissionRuleSet())
    monkeypatch.setattr(PermissionRuleSet, "evaluate", lambda *args: 1 / 0)
    result = await gate.check(tool_name="t", tool_args={}, reason="")
    assert not result.allowed and result.outcome == "permission_error"
    assert fake_audit_sink.rows[-1]["outcome"] == "permission_error"


async def test_cancelled_review_is_not_approved(monkeypatch, fake_audit_sink):
    guardian = AsyncMock()
    guardian.review.side_effect = asyncio.CancelledError
    monkeypatch.setattr("agent.approval.gate.get_approval_guardian", lambda: guardian)
    gate = ApprovalGate(PermissionRuleSet(default_effect=PermissionEffect.ASK))
    with pytest.raises(asyncio.CancelledError):
        await gate.check(tool_name="t", tool_args={}, reason="")
    assert fake_audit_sink.rows == []


def test_recursive_redaction_preserves_config_target():
    args = {"key": "smart_home_api_key", "value": "secret-value", "nested": [{"password": "abc"}], "keyboard": "normal"}
    result = redact_arguments(args)
    assert result["key"] == "smart_home_api_key"
    assert result["value"] == "***REDACTED***"
    assert result["nested"][0]["password"] == "***REDACTED***"
    assert result["keyboard"] == "normal"
    assert args["value"] == "secret-value"


def test_hot_reload_retains_last_good_rules(tmp_path, monkeypatch):
    from agent.approval.rules import save_rules
    path = tmp_path / "permission_rules.json"
    snapshot = PermissionRuleSet(default_effect=PermissionEffect.DENY)
    save_rules(snapshot, str(path))
    gate = ApprovalGate(snapshot)
    gate.reload_rules(str(path))
    path.write_text('{"unrecognized": []}')
    gate.reload_rules(str(path))
    assert gate.get_rule_set().default_effect == PermissionEffect.DENY
    monkeypatch.setattr("agent.approval.gate.save_rules", lambda _: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        gate.set_rule_set(PermissionRuleSet(), persist=True)
    assert gate.get_rule_set().default_effect == PermissionEffect.DENY
    copy = gate.get_rule_set()
    copy.default_effect = PermissionEffect.ALLOW
    assert gate.get_rule_set().default_effect == PermissionEffect.DENY
