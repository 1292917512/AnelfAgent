"""Guardian 自动审批 + 事件驱动等待测试。

覆盖：
- guardian 放行：ask 秒级自动批准，不发人工提示
- guardian 拒绝：有频道 → 升级人工（弹窗照常）；无频道 → 直接拒绝
- guardian 不可用：有频道 → 走人工流程；无频道 → 自主性放行（bypass 审计）
- wait_decision：事件驱动唤醒（决策即返回）、超时 EXPIRED、abort_check 中断 CANCELLED
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest

from agent.approval.gate import ApprovalGate
from agent.approval.guardian import ApprovalGuardian, GuardianVerdict
from agent.approval.manager import ApprovalManager
from agent.approval.rules import PermissionEffect, PermissionRule, PermissionRuleSet
from agent.approval.session import ApprovalDecision
from core.config import ConfigManager


class MockChannel:
    channel_id = "webui"

    def __init__(self):
        self.sent: List[str] = []
        self.prompts: List[Any] = []

    async def send_text(self, chat_id: str, text: str, **kwargs):
        self.sent.append(text)
        return json.dumps({"success": True})

    async def render_approval_prompt(self, ctx):
        self.prompts.append(ctx)
        return SimpleNamespace(channel=SimpleNamespace(channel_id=""))

    async def forward_message(self, request):
        return SimpleNamespace(success=True, error="")


class FakeGuardian:
    """可控的 guardian 替身（不触网；与真实实现一样先检查启用开关）。"""

    def __init__(self, verdict: Optional[GuardianVerdict]):
        self.verdict = verdict
        self.calls = 0

    async def review(self, **kwargs) -> Optional[GuardianVerdict]:
        from core.config import get_config_bool
        if not get_config_bool("approval_guardian_enabled", True):
            return None
        self.calls += 1
        return self.verdict


@pytest.fixture()
def guardian_on():
    ConfigManager.set("approval_guardian_enabled", True)
    yield
    ConfigManager.set("approval_guardian_enabled", True)


def _gate(monkeypatch, guardian: FakeGuardian, rules) -> ApprovalGate:
    monkeypatch.setattr("agent.approval.gate.get_approval_guardian", lambda: guardian)
    return ApprovalGate(manager=ApprovalManager(),
                        rule_set=PermissionRuleSet(rules=rules))


_ASK_RULE = [PermissionRule(pattern="write_file", effect=PermissionEffect.ASK)]


class TestGuardianFlow:
    async def test_guardian_approve_skips_human(self, monkeypatch, guardian_on):
        guardian = FakeGuardian(GuardianVerdict(approved=True, risk="low", rationale="常规写文件"))
        gate = _gate(monkeypatch, guardian, _ASK_RULE)
        ch = MockChannel()
        d = await gate.request_approval(
            tool_name="write_file", tool_args={"path": "a"}, reason="t",
            channel=ch, chat_id="c", user_id="u", timeout=5)
        assert d == ApprovalDecision.APPROVED
        assert guardian.calls == 1
        assert ch.prompts == []  # 未打扰用户
        assert await gate._manager.list_pending() == []

    async def test_guardian_deny_escalates_to_human(self, monkeypatch, guardian_on):
        guardian = FakeGuardian(GuardianVerdict(approved=False, risk="high", rationale="批量删除"))
        gate = _gate(monkeypatch, guardian, _ASK_RULE)
        ch = MockChannel()

        async def decide_later():
            await asyncio.sleep(0.2)
            pending = await gate._manager.list_pending()
            assert len(pending) == 1
            await gate.approve(pending[0].request.request_id, decided_by="tester")

        task = asyncio.create_task(decide_later())
        d = await gate.request_approval(
            tool_name="write_file", tool_args={"path": "a"}, reason="t",
            channel=ch, chat_id="c", user_id="u", timeout=5)
        await task
        assert d == ApprovalDecision.APPROVED  # 人工可越过 guardian
        assert len(ch.prompts) == 1

    async def test_guardian_deny_no_channel_denies(self, monkeypatch, guardian_on):
        """无人可问路径（reflect/心跳/子代理）：guardian 拒绝即拦截。"""
        guardian = FakeGuardian(GuardianVerdict(approved=False, risk="high", rationale="外泄凭据"))
        gate = _gate(monkeypatch, guardian, _ASK_RULE)
        d = await gate.request_approval(
            tool_name="write_file", tool_args={"path": "a"}, reason="t",
            channel=None, chat_id="scope", user_id="agent", timeout=5)
        assert d == ApprovalDecision.DENIED

    async def test_guardian_unavailable_no_channel_allows(self, monkeypatch, guardian_on):
        """无人可问 + guardian 不可用 → 自主性放行（不挂死）。"""
        guardian = FakeGuardian(None)
        gate = _gate(monkeypatch, guardian, _ASK_RULE)
        d = await gate.request_approval(
            tool_name="write_file", tool_args={"path": "a"}, reason="t",
            channel=None, chat_id="scope", user_id="agent", timeout=5)
        assert d == ApprovalDecision.APPROVED

    async def test_guardian_unavailable_with_channel_goes_human(self, monkeypatch, guardian_on):
        """有频道 + guardian 不可用 → 人工流程不受影响。"""
        guardian = FakeGuardian(None)
        gate = _gate(monkeypatch, guardian, _ASK_RULE)
        ch = MockChannel()

        async def decide_later():
            await asyncio.sleep(0.2)
            pending = await gate._manager.list_pending()
            await gate.approve(pending[0].request.request_id, decided_by="tester")

        task = asyncio.create_task(decide_later())
        d = await gate.request_approval(
            tool_name="write_file", tool_args={"path": "a"}, reason="t",
            channel=ch, chat_id="c", user_id="u", timeout=5)
        await task
        assert d == ApprovalDecision.APPROVED
        assert len(ch.prompts) == 1

    async def test_guardian_disabled_goes_straight_to_human(self, monkeypatch):
        """配置关闭时完全不经过 guardian。"""
        ConfigManager.set("approval_guardian_enabled", False)
        try:
            guardian = FakeGuardian(GuardianVerdict(approved=True))
            gate = _gate(monkeypatch, guardian, _ASK_RULE)
            ch = MockChannel()

            async def decide_later():
                await asyncio.sleep(0.2)
                pending = await gate._manager.list_pending()
                await gate.approve(pending[0].request.request_id, decided_by="tester")

            task = asyncio.create_task(decide_later())
            d = await gate.request_approval(
                tool_name="write_file", tool_args={"path": "a"}, reason="t",
                channel=ch, chat_id="c", user_id="u", timeout=5)
            await task
            assert d == ApprovalDecision.APPROVED
            assert guardian.calls == 0
            assert len(ch.prompts) == 1
        finally:
            ConfigManager.set("approval_guardian_enabled", True)


class TestGuardianBreaker:
    async def test_breaker_opens_after_consecutive_failures(self):
        guardian = ApprovalGuardian()
        ConfigManager.set("approval_guardian_breaker_cooldown", 60.0)
        try:
            for _ in range(3):
                guardian._record_failure()
            assert guardian._breaker_open_until > time.monotonic()
            # 熔断期内直接返回 None（不发起评审）
            verdict = await guardian.review(
                tool_name="t", tool_args={}, reason="", risk_level="low")
            assert verdict is None
        finally:
            ConfigManager.set("approval_guardian_breaker_cooldown", 300.0)

    def test_parse_verdict_tolerant(self):
        parse = ApprovalGuardian._parse_verdict
        assert parse('{"approve": true, "risk": "low", "rationale": "ok"}').approved is True
        assert parse('前言 {"approve": false, "risk": "high", "rationale": "危险"} 后缀').approved is False
        assert parse("不是 JSON") is None
        assert parse('{"approve": "yes"}') is None  # 非布尔不采信


class TestWaitDecision:
    async def _session(self, manager: ApprovalManager, timeout: float = 5.0):
        from agent.approval.policy import RiskLevel
        from agent.approval.session import ApprovalRequest
        req = ApprovalRequest(
            tool_name="write_file", tool_args={}, risk_level=RiskLevel.MEDIUM,
            reason="t", requester_channel="webui", requester_chat_id="c",
            requester_user_id="u", expires_at=time.time() + timeout,
        )
        return await manager.create_session(req)

    async def test_decision_wakes_waiter_immediately(self):
        """事件驱动：决策到达即唤醒，不等超时。"""
        manager = ApprovalManager()
        session = await self._session(manager, timeout=30.0)

        async def decide():
            await asyncio.sleep(0.1)
            await manager.approve(session.request.request_id, decided_by="tester")

        task = asyncio.create_task(decide())
        t0 = time.monotonic()
        d = await manager.wait_decision(session.request.request_id, timeout=30.0)
        elapsed = time.monotonic() - t0
        await task
        assert d == ApprovalDecision.APPROVED
        assert elapsed < 5.0  # 远超轮询时代的下限，事件驱动即时返回

    async def test_timeout_expires(self):
        manager = ApprovalManager()
        session = await self._session(manager)
        d = await manager.wait_decision(session.request.request_id, timeout=0.3)
        assert d == ApprovalDecision.EXPIRED

    async def test_abort_check_cancels(self):
        """中断信号命中：等待立即收束为 CANCELLED。"""
        manager = ApprovalManager()
        session = await self._session(manager, timeout=30.0)
        flag = {"aborted": False}

        async def raise_interrupt():
            await asyncio.sleep(0.2)
            flag["aborted"] = True

        task = asyncio.create_task(raise_interrupt())
        t0 = time.monotonic()
        d = await manager.wait_decision(
            session.request.request_id, timeout=30.0,
            abort_check=lambda: flag["aborted"],
        )
        elapsed = time.monotonic() - t0
        await task
        assert d == ApprovalDecision.CANCELLED
        assert elapsed < 5.0

    async def test_gate_abort_during_human_wait(self, monkeypatch):
        """端到端：人工等待期间 abort_check 命中，request_approval 返回 CANCELLED。"""
        ConfigManager.set("approval_guardian_enabled", False)
        try:
            gate = ApprovalGate(manager=ApprovalManager(),
                                rule_set=PermissionRuleSet(rules=_ASK_RULE))
            ch = MockChannel()
            flag = {"aborted": False}

            async def raise_interrupt():
                await asyncio.sleep(0.2)
                flag["aborted"] = True

            task = asyncio.create_task(raise_interrupt())
            d = await gate.request_approval(
                tool_name="write_file", tool_args={"path": "a"}, reason="t",
                channel=ch, chat_id="c", user_id="u", timeout=30,
                abort_check=lambda: flag["aborted"])
            await task
            assert d == ApprovalDecision.CANCELLED
        finally:
            ConfigManager.set("approval_guardian_enabled", True)
