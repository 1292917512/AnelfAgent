"""ApprovalGate 统一引擎集成测试 + 频道内批准回复路由测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from agent.approval.gate import ApprovalGate
from agent.approval.rules import PermissionEffect, PermissionRule, PermissionRuleSet
from agent.approval.session import ApprovalDecision


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


def _gate(rules) -> ApprovalGate:
    return ApprovalGate(rule_set=PermissionRuleSet(rules=rules))


class TestGateDecisions:
    async def test_auto_allow_no_prompt(self):
        gate = _gate([])
        ch = MockChannel()
        d = await gate.request_approval(
            tool_name="read_file", tool_args={"path": "a"}, reason="t",
            channel=ch, chat_id="c", user_id="u")
        assert d == ApprovalDecision.APPROVED
        assert ch.prompts == []

    async def test_auto_deny_notifies_user(self):
        gate = _gate([PermissionRule(pattern="delete_file", effect=PermissionEffect.DENY,
                                     description="禁止删除")])
        ch = MockChannel()
        d = await gate.request_approval(
            tool_name="delete_file", tool_args={"path": "a"}, reason="t",
            channel=ch, chat_id="c", user_id="u")
        assert d == ApprovalDecision.DENIED
        assert len(ch.sent) == 1
        assert "禁止删除" in ch.sent[0]
        assert "delete_file" in ch.sent[0]

    async def test_ask_flow_approved_via_manager(self):
        gate = _gate([PermissionRule(pattern="write_file", effect=PermissionEffect.ASK)])
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
        assert d == ApprovalDecision.APPROVED
        assert len(ch.prompts) == 1

    async def test_ask_timeout_denies_and_notifies(self):
        gate = _gate([PermissionRule(pattern="write_file", effect=PermissionEffect.ASK,
                                     timeout_seconds=0.3)])
        ch = MockChannel()
        d = await gate.request_approval(
            tool_name="write_file", tool_args={"path": "a"}, reason="t",
            channel=ch, chat_id="c", user_id="u")
        assert d == ApprovalDecision.DENIED
        assert any("超时" in m for m in ch.sent)

    async def test_remember_session_creates_session_rule(self):
        gate = _gate([PermissionRule(pattern="write_file", effect=PermissionEffect.ASK)])
        ch = MockChannel()

        async def decide_later():
            await asyncio.sleep(0.2)
            pending = await gate._manager.list_pending()
            await gate.approve(pending[0].request.request_id, decided_by="t",
                               remember="session")

        task = asyncio.create_task(decide_later())
        await gate.request_approval(
            tool_name="write_file", tool_args={"path": "a"}, reason="t",
            channel=ch, chat_id="c", user_id="u", timeout=5)
        await task
        # 第二次调用：会话规则放行，不再询问
        ch2 = MockChannel()
        d = await gate.request_approval(
            tool_name="write_file", tool_args={"path": "b"}, reason="t",
            channel=ch2, chat_id="c", user_id="u")
        assert d == ApprovalDecision.APPROVED
        assert ch2.prompts == []

    async def test_remember_always_persists(self, tmp_path, monkeypatch):
        import agent.approval.rules as rules_mod
        rules_file = tmp_path / "rules.json"
        monkeypatch.setattr(rules_mod, "RULES_PATH", str(rules_file))
        # save_rules 使用默认参数路径 → 打补丁到 gate 内部调用
        saved: List[PermissionRuleSet] = []
        monkeypatch.setattr("agent.approval.gate.save_rules", lambda rs: saved.append(rs))

        gate = _gate([PermissionRule(pattern="write_file", effect=PermissionEffect.ASK)])
        ch = MockChannel()

        async def decide_later():
            await asyncio.sleep(0.2)
            pending = await gate._manager.list_pending()
            await gate.approve(pending[0].request.request_id, decided_by="t",
                               remember="always")

        task = asyncio.create_task(decide_later())
        await gate.request_approval(
            tool_name="write_file", tool_args={"path": "a"}, reason="t",
            channel=ch, chat_id="c", user_id="u", timeout=5)
        await task
        assert saved and any(r.effect == PermissionEffect.ALLOW for r in saved[0].rules)


class TestApprovalReplyRouting:
    async def test_approve_command_resolves_session(self, monkeypatch):
        from agent.approval import get_approval_gate
        from agent.runtime.agent_app import _try_resolve_approval

        gate = get_approval_gate()
        from agent.approval.policy import RiskLevel
        from agent.approval.session import ApprovalRequest
        import time as _time
        req = ApprovalRequest(
            tool_name="write_file", tool_args={}, risk_level=RiskLevel.HIGH,
            reason="t", requester_channel="webui", requester_chat_id="c",
            requester_user_id="u", expires_at=_time.time() + 60,
        )
        session = await gate._manager.create_session(req)

        payload = {"content": f"approve {req.request_id}", "user_id": "u1",
                   "adapter_key": "webui", "group_id": 0}
        handled = await _try_resolve_approval(payload)
        assert handled is True
        resolved = await gate._manager.get_session(req.request_id)
        assert resolved.decision == ApprovalDecision.APPROVED

    async def test_non_approval_message_passes(self):
        from agent.runtime.agent_app import _try_resolve_approval
        assert await _try_resolve_approval({"content": "今天天气怎么样"}) is False
        assert await _try_resolve_approval({"content": "approve nonexistent123"}) is False

    async def test_colon_format_resolves(self):
        from agent.approval import get_approval_gate
        from agent.approval.session import ApprovalRequest
        from agent.approval.policy import RiskLevel
        from agent.runtime.agent_app import _try_resolve_approval
        import time as _time

        gate = get_approval_gate()
        req = ApprovalRequest(
            tool_name="x", tool_args={}, risk_level=RiskLevel.LOW,
            reason="t", requester_channel="telegram", requester_chat_id="c",
            requester_user_id="u", expires_at=_time.time() + 60,
        )
        await gate._manager.create_session(req)
        handled = await _try_resolve_approval({
            "content": f"deny:{req.request_id}", "user_id": "u1", "adapter_key": "telegram"})
        assert handled is True
        resolved = await gate._manager.get_session(req.request_id)
        assert resolved.decision == ApprovalDecision.DENIED
