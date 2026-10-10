"""Guardian 的截止时间、模型配置、解析、熔断和取消契约。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.approval.guardian import ApprovalGuardian, GuardianVerdict, _Settings


@pytest.fixture()
def settings(monkeypatch):
    config = _Settings(True, "", "low", 30.0, 300.0, 5)
    state = {"current": config}
    monkeypatch.setattr(_Settings, "read", lambda: state["current"])
    return state


@pytest.fixture()
def manager(monkeypatch, fake_audit_sink):
    manager = SimpleNamespace(
        get_client_by_id=lambda _: None,
        chat_with_fallback=AsyncMock(return_value=SimpleNamespace(content='{"approve":true,"risk":"low","rationale":"safe"}')),
    )
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)
    return manager


async def test_timeout_configuration_reaches_model(settings, manager, monkeypatch):
    monkeypatch.setattr("agent.approval.guardian.time", SimpleNamespace(monotonic=lambda: 119.3))
    verdict = await ApprovalGuardian().review(tool_name="t", tool_args={}, reason="", risk_level="low")
    assert verdict.approved is True
    kwargs = manager.chat_with_fallback.call_args.kwargs
    assert 29 < kwargs["timeout"] <= 30
    assert kwargs["max_retries"] == 0 and kwargs["purpose"] == "guardian"


async def test_timeout_cancels_inflight_request(settings, manager):
    settings["current"] = replace(settings["current"], timeout=0.01)
    cancelled = asyncio.Event()
    async def hang(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    manager.chat_with_fallback.side_effect = hang
    verdict = await ApprovalGuardian().review(tool_name="t", tool_args={}, reason="", risk_level="high")
    assert verdict.approved is None and "总时限" in verdict.rationale
    assert cancelled.is_set()


async def test_cancellation_not_counted_as_failure(settings, manager):
    guardian = ApprovalGuardian()
    manager.chat_with_fallback.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await guardian.review(tool_name="t", tool_args={}, reason="", risk_level="high")
    assert guardian._consecutive_failures == 0


async def test_breaker_and_configuration_recovery(settings, manager):
    guardian = ApprovalGuardian()
    manager.chat_with_fallback.side_effect = OSError
    for _ in range(4):
        assert (await guardian.review(tool_name="t", tool_args={}, reason="", risk_level="high")).approved is None
    assert manager.chat_with_fallback.await_count == 3
    settings["current"] = replace(settings["current"], timeout=40)
    manager.chat_with_fallback.side_effect = None
    assert (await guardian.review(tool_name="t", tool_args={}, reason="", risk_level="high")).approved is True
    assert guardian._consecutive_failures == 0


async def test_disabled_skips_model(settings, manager):
    settings["current"] = replace(settings["current"], enabled=False)
    result = await ApprovalGuardian().review(tool_name="t", tool_args={}, reason="", risk_level="high")
    assert result.approved is None and "关闭" in result.rationale
    manager.chat_with_fallback.assert_not_awaited()


async def test_model_override_and_missing_model(settings, manager):
    settings["current"] = replace(settings["current"], model="reviewer")
    result = await ApprovalGuardian().review(tool_name="t", tool_args={}, reason="", risk_level="low")
    assert result.approved is None
    manager.chat_with_fallback.assert_not_awaited()
    client = SimpleNamespace(config=SimpleNamespace(enabled=True))
    manager.get_client_by_id = lambda _: client
    result = await ApprovalGuardian().review(tool_name="t", tool_args={}, reason="", risk_level="low")
    assert result.approved is True
    assert manager.chat_with_fallback.call_args.kwargs["client"] is client


async def test_history_identity_includes_channel(settings, manager, fake_audit_sink):
    fake_audit_sink.rows = [{"tool_name":"t", "outcome":"guardian_approved", "user_id":"123", "channel_id":"qq", "reason":"previous"}]
    await ApprovalGuardian().review(tool_name="t", tool_args={}, reason="", risk_level="low", channel_id="webui", user_id="123")
    text = manager.chat_with_fallback.call_args.args[0][1]["content"]
    assert '"same_actor": false' in text
    assert "不构成授权" in text


@pytest.mark.parametrize("text", ['[]', 'null', '{"approve":"yes"}', '{"approve":true,"risk":"unknown","rationale":"ok"}', '{"approve":true,"risk":"low"}'])
def test_invalid_decisions_are_not_accepted(text):
    assert ApprovalGuardian._parse_verdict(text) is None


def test_fenced_json_is_accepted():
    assert ApprovalGuardian._parse_verdict('```json\n{"approve":false,"risk":"high","rationale":"danger"}\n```') == GuardianVerdict(False,"high","danger")
