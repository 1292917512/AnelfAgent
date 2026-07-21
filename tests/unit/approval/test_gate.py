"""批准机制测试。"""

import asyncio
import time

import pytest

from agent.approval import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalManager,
    ApprovalPolicy,
    ApprovalPolicySet,
    ApprovalRequest,
    RiskLevel,
    get_approval_gate,
    get_approval_manager,
)


@pytest.fixture
def policy_set():
    """测试用策略集。"""
    return ApprovalPolicySet(
        policies=[
            ApprovalPolicy(
                tool_name_pattern="shell.*",
                risk_level=RiskLevel.CRITICAL,
                requires_approval=True,
                timeout_seconds=2.0,  # 短超时便于测试
            ),
            ApprovalPolicy(
                tool_name_pattern="filesystem.*",
                risk_level=RiskLevel.HIGH,
                requires_approval=True,
                timeout_seconds=2.0,
            ),
            ApprovalPolicy(
                tool_name_pattern="memory.*",
                risk_level=RiskLevel.LOW,
                requires_approval=False,
            ),
        ],
    )


@pytest.mark.asyncio
async def test_policy_matching(policy_set):
    """测试策略匹配。"""
    # 精确匹配
    p = policy_set.match("shell.exec")
    assert p is not None
    assert p.risk_level == RiskLevel.CRITICAL
    assert p.requires_approval is True

    # glob 匹配
    p = policy_set.match("filesystem.write_file")
    assert p is not None
    assert p.risk_level == RiskLevel.HIGH

    # 无需批准
    p = policy_set.match("memory.query")
    assert p is not None
    assert p.requires_approval is False

    # 未匹配（返回 None 或 default_policy）
    p = policy_set.match("unknown.tool")
    assert p is None or p == policy_set.default_policy


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
async def test_trust_mechanism():
    """测试信任机制（trust_after_n_approvals）。"""
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

    # 模拟 2 次批准
    for i in range(2):
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
        session = await manager.create_session(request)
        await manager.approve(request.request_id, decided_by="admin")

    # 达到阈值
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
async def test_manager_stats():
    """测试统计信息。"""
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
