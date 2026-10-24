"""Guardian 的截止时间、模型配置、解析、熔断和取消契约。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.approval.guardian import ApprovalGuardian, GuardianVerdict, _Settings


@pytest.fixture()
def settings(monkeypatch):
    config = _Settings(True, "", "low", 30.0, 300.0, 5, backend="llm")
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


async def test_timeout_cancels_inflight_request(settings, manager, monkeypatch):
    timeout = asyncio.timeout(None)
    monkeypatch.setattr("agent.approval.guardian.asyncio", SimpleNamespace(timeout=lambda _: timeout))
    cancelled = asyncio.Event()
    async def hang(*args, **kwargs):
        try:
            timeout.reschedule(asyncio.get_running_loop().time())
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    manager.chat_with_fallback.side_effect = hang
    verdict = await ApprovalGuardian().review(tool_name="t", tool_args={}, reason="", risk_level="high")
    assert verdict.approved is None and "总时限" in verdict.rationale
    manager.chat_with_fallback.assert_awaited_once()
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


@pytest.fixture()
def sdk(settings, monkeypatch, fake_audit_sink):
    settings["current"] = replace(settings["current"], backend="sdk")
    config = SimpleNamespace(enabled=True, native_configured=True, timeout=5.0)
    monkeypatch.setattr("agent.judgment.engine.load_config", lambda: config)
    api = SimpleNamespace(judge=AsyncMock())
    monkeypatch.setattr("agent.llm.model_api.get_model_api", lambda: api)
    return api, config


@pytest.mark.parametrize("choice,probability,approved", [
    ("allow", 0.9, True), ("exfiltration", 0.9, False),
    ("exfiltration", 0.6, True), ("uncertain", 0.9, True),
])
async def test_sdk_reviews_without_chat_fallback(settings, sdk, manager, choice, probability, approved):
    from agent.judgment.types import ChoiceAnswer, JudgmentReport, JudgmentSource

    api, _ = sdk
    options = {"allow", "destructive", "exfiltration", "persistence", "uncertain"}
    probabilities = {key: probability if key == choice else (1 - probability) / 4 for key in options}
    api.judge.return_value = JudgmentReport(source=JudgmentSource.TYPESAFE, answers={
        "decision": ChoiceAnswer(choice=choice, probabilities=probabilities, confidence=0.9),
    })
    verdict = await ApprovalGuardian().review(tool_name="vault_reveal", tool_args={}, reason="", risk_level="critical")
    assert verdict.approved is approved
    assert api.judge.call_args.kwargs["native_only"] is True
    assert 0 < api.judge.call_args.kwargs["timeout"] <= 5
    manager.chat_with_fallback.assert_not_awaited()


@pytest.mark.parametrize("failure", ["unconfigured", "disabled", "timeout", "invalid"])
async def test_sdk_unavailable_defaults_to_rule_execution(settings, sdk, manager, failure):
    from agent.judgment.types import JudgmentReport, JudgmentSource

    api, config = sdk
    if failure == "unconfigured":
        config.native_configured = False
    elif failure == "disabled":
        config.enabled = False
    elif failure == "timeout":
        api.judge.side_effect = TimeoutError
    else:
        api.judge.return_value = JudgmentReport(source=JudgmentSource.TYPESAFE, answers={}, missing=["decision"])
    verdict = await ApprovalGuardian().review(tool_name="vault_reveal", tool_args={}, reason="", risk_level="critical")
    assert verdict.approved is None
    manager.chat_with_fallback.assert_not_awaited()
    if failure in {"unconfigured", "disabled"}:
        api.judge.assert_not_awaited()


def test_default_review_backend_is_sdk():
    from agent.llm.reasoning import CANONICAL_EFFORTS
    from core.config import ConfigRegistry

    settings = _Settings.read()
    assert settings.backend == "sdk" and not settings.enabled
    assert settings.timeout == 60
    item = ConfigRegistry.get_item("approval_guardian_backend")
    assert item is not None
    assert item.value_type == "enum"
    assert item.enum_options == ["sdk", "llm"]
    effort = ConfigRegistry.get_item("approval_guardian_effort")
    assert effort is not None and effort.type_name == "enum"
    assert effort.enum_options == list(CANONICAL_EFFORTS)


@pytest.mark.parametrize("text", ['[]', 'null', '{"approve":"yes"}', '{"approve":true,"risk":"unknown","rationale":"ok"}', '{"approve":true,"risk":"low"}'])
def test_invalid_decisions_are_not_accepted(text):
    assert ApprovalGuardian._parse_verdict(text) is None


def test_fenced_json_is_accepted():
    assert ApprovalGuardian._parse_verdict('```json\n{"approve":false,"risk":"high","rationale":"danger"}\n```') == GuardianVerdict(False,"high","danger")
