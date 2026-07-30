"""ApprovalGate 统一引擎集成测试 + 频道内批准回复路由 + 参数脱敏/授权校验。"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any, List

import pytest

from agent.approval.gate import ApprovalGate
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
        import time as _time

        from agent.approval.policy import RiskLevel
        from agent.approval.session import ApprovalRequest
        req = ApprovalRequest(
            tool_name="write_file", tool_args={}, risk_level=RiskLevel.HIGH,
            reason="t", requester_channel="webui", requester_chat_id="c",
            requester_user_id="u", expires_at=_time.time() + 60,
        )
        await gate._manager.create_session(req)

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
        import time as _time

        from agent.approval import get_approval_gate
        from agent.approval.policy import RiskLevel
        from agent.approval.session import ApprovalRequest
        from agent.runtime.agent_app import _try_resolve_approval

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


# ==================================================================
# 参数脱敏边界匹配
# ==================================================================

@pytest.fixture
def sanitize_gate() -> ApprovalGate:
    return ApprovalGate(manager=ApprovalManager(), rule_set=PermissionRuleSet())


class TestSanitizeBoundary:
    def test_exact_sensitive_redacted(self, sanitize_gate):
        out = sanitize_gate._sanitize_args({"api_key": "x", "token": "y", "password": "z"})
        assert out["api_key"] == "***REDACTED***"
        assert out["token"] == "***REDACTED***"
        assert out["password"] == "***REDACTED***"

    def test_bounded_sensitive_redacted(self, sanitize_gate):
        out = sanitize_gate._sanitize_args({
            "my_key": "a", "key_id": "b", "auth_token": "c", "x-secret-y": "d",
        })
        assert out["my_key"] == "***REDACTED***"
        assert out["key_id"] == "***REDACTED***"
        assert out["auth_token"] == "***REDACTED***"
        assert out["x-secret-y"] == "***REDACTED***"

    def test_substring_not_redacted(self, sanitize_gate):
        """子串误伤修复：monkey/keyboard/tokenize 不应被脱敏。"""
        out = sanitize_gate._sanitize_args({
            "monkey": "a", "keyboard": "b", "tokenize": "c", "apikey_like": "d",
        })
        assert out["monkey"] == "a"
        assert out["keyboard"] == "b"
        assert out["tokenize"] == "c"
        # apikey（无分隔边界）不命中精确词 key/api_key
        assert out["apikey_like"] == "d"

    def test_plain_arg_passthrough(self, sanitize_gate):
        out = sanitize_gate._sanitize_args({"path": "/tmp/x", "count": 3})
        assert out["path"] == "/tmp/x"
        assert out["count"] == 3


# ==================================================================
# 频道内审批授权校验
# ==================================================================

@pytest.fixture
def admin_config():
    """隔离 ConfigManager 与模块级提示标志，测试后还原。"""
    import agent.runtime.agent_app as app_mod

    ConfigManager.set("approval_admin_users", [])
    original_flag = app_mod._approval_admin_hint_logged
    yield app_mod
    ConfigManager.set("approval_admin_users", [])
    app_mod._approval_admin_hint_logged = original_flag


class TestApprovalAdminCheck:
    def test_empty_whitelist_allows_anyone(self, admin_config):
        app_mod = admin_config
        assert app_mod._is_approval_admin("anyone", "telegram") is True

    def test_whitelist_global_user(self, admin_config):
        app_mod = admin_config
        ConfigManager.set("approval_admin_users", ["admin"])
        assert app_mod._is_approval_admin("admin", "telegram") is True
        assert app_mod._is_approval_admin("guest", "telegram") is False

    def test_whitelist_channel_scoped_user(self, admin_config):
        app_mod = admin_config
        ConfigManager.set("approval_admin_users", ["telegram:admin"])
        assert app_mod._is_approval_admin("admin", "telegram") is True
        # 同一用户在其他频道不生效
        assert app_mod._is_approval_admin("admin", "webui") is False
        assert app_mod._is_approval_admin("guest", "telegram") is False


async def _make_pending_session(request_id: str) -> None:
    from agent.approval import get_approval_manager
    from agent.approval.policy import RiskLevel
    from agent.approval.session import ApprovalRequest

    manager = get_approval_manager()
    request = ApprovalRequest(
        tool_name="run_shell_command",
        tool_args={"command": "ls"},
        risk_level=RiskLevel.HIGH,
        reason="test",
        requester_channel="telegram",
        requester_chat_id="chat_1",
        requester_user_id="requester",
        expires_at=time.time() + 60.0,
    )
    session = await manager.create_session(request)
    # 覆盖 request_id 便于构造审批指令
    async with manager._lock:
        manager._sessions.pop(session.request.request_id, None)
        session.request.request_id = request_id
        manager._sessions[request_id] = session


@pytest.mark.asyncio
async def test_non_admin_approval_passes_through(admin_config):
    """非白名单用户的审批指令按普通消息放行，会话保持挂起。"""
    app_mod = admin_config
    ConfigManager.set("approval_admin_users", ["admin"])
    from agent.approval import get_approval_manager

    await _make_pending_session("req_nonadmin")
    payload = {
        "content": "approve req_nonadmin",
        "user_id": "guest",
        "adapter_key": "telegram",
    }
    resolved = await app_mod._try_resolve_approval(payload)
    assert resolved is False  # 未拦截，按普通消息放行

    session = await get_approval_manager().get_session("req_nonadmin")
    assert session is not None and session.is_pending()


@pytest.mark.asyncio
async def test_admin_approval_resolves(admin_config):
    """白名单用户的审批指令正常生效。"""
    app_mod = admin_config
    ConfigManager.set("approval_admin_users", ["admin"])
    from agent.approval import get_approval_manager

    await _make_pending_session("req_admin")
    payload = {
        "content": "approve req_admin",
        "user_id": "admin",
        "adapter_key": "telegram",
    }
    resolved = await app_mod._try_resolve_approval(payload)
    assert resolved is True

    session = await get_approval_manager().get_session("req_admin")
    assert session is not None and not session.is_pending()
