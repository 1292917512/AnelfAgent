"""批准机制测试。"""

import asyncio
import time

import pytest

from agent.approval import (
    ApprovalDecision,
    ApprovalManager,
    ApprovalPolicy,
    ApprovalRequest,
    RiskLevel,
    get_approval_gate,
    get_approval_manager,
)


class _FakeAuditSink:
    """审批审计的内存假 sqlite（与 SqliteBackend 审计方法同接口）。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._seq = 0

    async def append_approval_audit(self, record: dict) -> None:
        self._seq += 1
        self.rows.append({"id": self._seq, **record})

    async def count_approval_outcomes(self, tool_name: str, user_id: str, outcome: str) -> int:
        return sum(
            1 for r in self.rows
            if r.get("tool_name") == tool_name
            and r.get("user_id") == user_id
            and r.get("outcome") == outcome
        )

    async def approval_audit_stats(self) -> dict:
        by: dict[str, int] = {}
        for r in self.rows:
            by[r.get("outcome", "")] = by.get(r.get("outcome", ""), 0) + 1
        return {"total": len(self.rows), "by_outcome": by}

    async def list_approval_audit(self, limit: int = 50, offset: int = 0, tool_name: str = "") -> list[dict]:
        rows = [r for r in self.rows if not tool_name or r.get("tool_name") == tool_name]
        return list(reversed(rows))[offset:offset + limit]


@pytest.fixture()
def fake_audit_sink(monkeypatch: pytest.MonkeyPatch) -> _FakeAuditSink:
    """把审批审计数据面指向内存假 sqlite（隔离真实 DB）。"""
    import agent.approval.audit as audit_mod

    sink = _FakeAuditSink()
    monkeypatch.setattr(audit_mod, "_audit_sink", lambda: sink)
    return sink


async def _drain_audit_bg() -> None:
    """让即发即忘的审计后台任务跑完（record_decision_bg 经 create_task）。"""
    for _ in range(4):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_approval_session_lifecycle():
    """测试批准会话生命周期。"""
    manager = ApprovalManager()

    request = ApprovalRequest(
        tool_name="test.tool",
        tool_args={"arg": "value"},
        risk_level=RiskLevel.HIGH,
        reason="test",
        requester_channel="test",
        requester_chat_id="chat_1",
        requester_user_id="user_1",
        expires_at=time.time() + 10.0,
    )

    session = await manager.create_session(request)
    assert session.is_pending() is True
    assert session.status == "pending"

    # 批准
    ok = await manager.approve(request.request_id, decided_by="admin")
    assert ok is True

    session = await manager.get_session(request.request_id)
    assert session.status == "resolved"
    assert session.decision == ApprovalDecision.APPROVED
    assert session.decided_by == "admin"


@pytest.mark.asyncio
async def test_approval_timeout():
    """测试批准超时。"""
    manager = ApprovalManager()

    request = ApprovalRequest(
        tool_name="test.tool",
        tool_args={},
        risk_level=RiskLevel.HIGH,
        reason="test",
        requester_channel="test",
        requester_chat_id="chat_1",
        requester_user_id="user_1",
        expires_at=time.time() + 0.1,  # 立即过期
    )

    session = await manager.create_session(request)
    await asyncio.sleep(0.2)

    assert session.is_expired() is True
    assert session.is_pending() is False


@pytest.mark.asyncio
async def test_auto_approve_deny():
    """测试自动批准/拒绝。"""
    policy = ApprovalPolicy(
        tool_name_pattern="test.*",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        auto_approve_users=["admin"],
        auto_deny_users=["blocked"],
    )

    assert policy.is_auto_approved("admin") is True
    assert policy.is_auto_approved("user") is False
    assert policy.is_auto_denied("blocked") is True
    assert policy.is_auto_denied("user") is False


@pytest.mark.asyncio
async def test_trust_mechanism(fake_audit_sink: _FakeAuditSink):
    """测试信任机制（trust_after_n_approvals，计数来自审计账本）。"""
    manager = ApprovalManager()
    policy = ApprovalPolicy(
        tool_name_pattern="test.*",
        risk_level=RiskLevel.HIGH,
        requires_approval=True,
        trust_after_n_approvals=2,
    )

    # 未达到阈值
    trusted = await manager.is_trusted("test.tool", "user_1", policy)
    assert trusted is False

    # 模拟 2 次批准（决策经审计账本持久化）
    for _ in range(2):
        request = ApprovalRequest(
            tool_name="test.tool",
            tool_args={},
            risk_level=RiskLevel.HIGH,
            reason="test",
            requester_channel="test",
            requester_chat_id="chat_1",
            requester_user_id="user_1",
            expires_at=time.time() + 10.0,
        )
        await manager.create_session(request)
        await manager.approve(request.request_id, decided_by="admin")
    await _drain_audit_bg()

    # 达到阈值（账本累计计数，重启不清零——由账本持久性保证）
    trusted = await manager.is_trusted("test.tool", "user_1", policy)
    assert trusted is True


@pytest.mark.asyncio
async def test_manager_cleanup():
    """测试过期会话清理。"""
    manager = ApprovalManager()

    # 创建 3 个会话，其中 2 个过期
    for i in range(3):
        request = ApprovalRequest(
            tool_name=f"test.tool{i}",
            tool_args={},
            risk_level=RiskLevel.HIGH,
            reason="test",
            requester_channel="test",
            requester_chat_id="chat_1",
            requester_user_id="user_1",
            expires_at=time.time() + (0.1 if i < 2 else 10.0),
        )
        await manager.create_session(request)

    await asyncio.sleep(0.2)
    cleaned = await manager.cleanup_expired()
    assert cleaned == 2

    pending = await manager.list_pending()
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_manager_stats(fake_audit_sink: _FakeAuditSink):
    """测试统计信息（决策聚合来自审计账本）。"""
    manager = ApprovalManager()

    # 创建并决策几个会话
    for i, decision in enumerate(["approved", "denied", "approved"]):
        request = ApprovalRequest(
            tool_name=f"test.tool{i}",
            tool_args={},
            risk_level=RiskLevel.HIGH,
            reason="test",
            requester_channel="test",
            requester_chat_id="chat_1",
            requester_user_id="user_1",
            expires_at=time.time() + 10.0,
        )
        await manager.create_session(request)
        if decision == "approved":
            await manager.approve(request.request_id)
        else:
            await manager.deny(request.request_id)
    await _drain_audit_bg()

    stats = await manager.get_stats()
    assert stats["pending_count"] == 0
    assert stats["history_size"] == 3
    assert stats["history_by_decision"]["approved"] == 2
    assert stats["history_by_decision"]["denied"] == 1


@pytest.mark.asyncio
async def test_gate_singleton():
    """测试全局单例。"""
    gate1 = get_approval_gate()
    gate2 = get_approval_gate()
    assert gate1 is gate2

    manager1 = get_approval_manager()
    manager2 = get_approval_manager()
    assert manager1 is manager2
