"""审批审计持久化（agent.approval.audit + sqlite approval_audit）单元测试。"""

from __future__ import annotations

import asyncio
import time

import pytest
from test_gate import _drain_audit_bg, _FakeAuditSink  # noqa: F401  (fixture 复用)

import agent.approval.audit as audit_mod
from agent.approval import (
    ApprovalManager,
    ApprovalRequest,
    RiskLevel,
)
from agent.approval.rules import (
    PermissionEffect,
    PermissionRule,
    PermissionRuleSet,
)
from agent.approval.rules import (
    RiskLevel as RuleRiskLevel,
)
from agent.approval.session import ApprovalDecision


@pytest.fixture()
def sink(monkeypatch: pytest.MonkeyPatch) -> _FakeAuditSink:
    fake = _FakeAuditSink()
    monkeypatch.setattr(audit_mod, "_audit_sink", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_unknown_outcome_dropped(sink: _FakeAuditSink) -> None:
    """超出词表的 outcome 说明调用方语义漂移：记 WARNING 后丢弃。"""
    await audit_mod.record_decision(tool_name="t", outcome="mystery")
    assert sink.rows == []


@pytest.mark.asyncio
async def test_trusted_not_counted_as_approved(sink: _FakeAuditSink) -> None:
    """信任放行（trusted）不计入 approved 计数——防自动放行自我强化。"""
    await audit_mod.record_decision(tool_name="t", outcome="approved", user_id="u1")
    await audit_mod.record_decision(tool_name="t", outcome="trusted", user_id="u1")
    count = await audit_mod.count_approvals("t", "u1")
    assert count == 1


@pytest.mark.asyncio
async def test_resolve_writes_audit_with_context(sink: _FakeAuditSink) -> None:
    """manager.resolve 是人工流审计汇聚点：完整上下文随决策落账本。"""
    manager = ApprovalManager()
    request = ApprovalRequest(
        tool_name="write_file",
        tool_args={"file_path": "/tmp/x", "content": "hi"},
        risk_level=RiskLevel.HIGH,
        reason="high risk write",
        requester_channel="qq",
        requester_chat_id="c1",
        requester_user_id="u1",
        expires_at=time.time() + 10.0,
        matched_rule="write_file(*)",
    )
    await manager.create_session(request)
    await manager.approve(request.request_id, decided_by="webui")
    await _drain_audit_bg()

    assert len(sink.rows) == 1
    row = sink.rows[0]
    assert row["tool_name"] == "write_file"
    assert row["outcome"] == "approved"
    assert row["decided_by"] == "webui"
    assert row["user_id"] == "u1"
    assert row["matched_rule"] == "write_file(*)"
    assert row["risk_level"] == "high"
    assert "file_path" in row["args_json"]


@pytest.mark.asyncio
async def test_gate_rule_deny_audited(sink: _FakeAuditSink) -> None:
    """规则自动拒绝经 gate 落账本（decided_by=rule，携带命中规则）。"""
    from agent.approval.gate import ApprovalGate

    rule_set = PermissionRuleSet(
        rules=[PermissionRule(
            pattern="dangerous_tool(*)", effect=PermissionEffect.DENY,
            risk_level=RuleRiskLevel.HIGH,
        )],
    )
    gate = ApprovalGate(manager=ApprovalManager(), rule_set=rule_set)

    class _Channel:
        channel_id = "test"

        async def send_text(self, chat_id: str, text: str) -> None:
            pass

    decision = await gate.request_approval(
        tool_name="dangerous_tool", tool_args={"x": 1}, reason="test",
        channel=_Channel(), chat_id="c1", user_id="u1",  # type: ignore[arg-type]
    )
    await _drain_audit_bg()

    assert decision == ApprovalDecision.DENIED
    assert len(sink.rows) == 1
    assert sink.rows[0]["outcome"] == "denied"
    assert sink.rows[0]["decided_by"] == "rule"
    assert "dangerous_tool" in sink.rows[0]["matched_rule"]


@pytest.mark.asyncio
async def test_history_pagination(sink: _FakeAuditSink) -> None:
    """历史分页按时间倒序（最新在前），可按工具名过滤。"""
    for _ in range(5):
        await audit_mod.record_decision(tool_name="t", outcome="approved", user_id="u")
        await asyncio.sleep(0)
    page1 = await audit_mod.list_history(limit=2, offset=0)
    page2 = await audit_mod.list_history(limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert page1[0]["id"] > page2[0]["id"]  # 倒序

    for _ in range(3):
        await audit_mod.record_decision(tool_name="other", outcome="denied", user_id="u")
    filtered = await audit_mod.list_history(limit=10, tool_name="other")
    assert len(filtered) == 3
    assert all(r["tool_name"] == "other" for r in filtered)


@pytest.mark.asyncio
async def test_stats_aggregates_by_outcome(sink: _FakeAuditSink) -> None:
    """stats 按 outcome 聚合（统计页数据源）。"""
    await audit_mod.record_decision(tool_name="t", outcome="approved", user_id="u")
    await audit_mod.record_decision(tool_name="t", outcome="approved", user_id="u")
    await audit_mod.record_decision(tool_name="t", outcome="denied", user_id="u")
    stats = await audit_mod.stats()
    assert stats["total"] == 3
    assert stats["by_outcome"]["approved"] == 2
    assert stats["by_outcome"]["denied"] == 1
