"""审批与鉴权安全修复测试：参数脱敏边界 / 频道内审批授权校验。"""

from __future__ import annotations

import time

import pytest

from agent.approval import ApprovalGate, ApprovalManager, ApprovalRequest, RiskLevel
from agent.approval.rules import PermissionRuleSet
from core.config import ConfigManager


# ----------------------------------------------------------------------
# 参数脱敏边界匹配（fix 5）
# ----------------------------------------------------------------------


@pytest.fixture
def gate() -> ApprovalGate:
    return ApprovalGate(manager=ApprovalManager(), rule_set=PermissionRuleSet())


class TestSanitizeBoundary:
    def test_exact_sensitive_redacted(self, gate):
        out = gate._sanitize_args({"api_key": "x", "token": "y", "password": "z"})
        assert out["api_key"] == "***REDACTED***"
        assert out["token"] == "***REDACTED***"
        assert out["password"] == "***REDACTED***"

    def test_bounded_sensitive_redacted(self, gate):
        out = gate._sanitize_args({
            "my_key": "a", "key_id": "b", "auth_token": "c", "x-secret-y": "d",
        })
        assert out["my_key"] == "***REDACTED***"
        assert out["key_id"] == "***REDACTED***"
        assert out["auth_token"] == "***REDACTED***"
        assert out["x-secret-y"] == "***REDACTED***"

    def test_substring_not_redacted(self, gate):
        """子串误伤修复：monkey/keyboard/tokenize 不应被脱敏。"""
        out = gate._sanitize_args({
            "monkey": "a", "keyboard": "b", "tokenize": "c", "apikey_like": "d",
        })
        assert out["monkey"] == "a"
        assert out["keyboard"] == "b"
        assert out["tokenize"] == "c"
        # apikey（无分隔边界）不命中精确词 key/api_key
        assert out["apikey_like"] == "d"

    def test_plain_arg_passthrough(self, gate):
        out = gate._sanitize_args({"path": "/tmp/x", "count": 3})
        assert out["path"] == "/tmp/x"
        assert out["count"] == 3


# ----------------------------------------------------------------------
# 频道内审批授权校验（fix 3）
# ----------------------------------------------------------------------


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
