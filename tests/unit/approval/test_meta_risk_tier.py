"""工具元数据风险层测试（求值管线第 6 层：CRITICAL 兜底升级 ask）。

优先级语义：声明式规则（含会话级）> 工具 metadata > 默认效果。
"""

from __future__ import annotations

import pytest

from agent.approval.policy import RiskLevel
from agent.approval.rules import (
    _META_RISK_PATTERN,
    PermissionDecision,
    PermissionEffect,
    PermissionRule,
    PermissionRuleSet,
    tool_meta_risk_rule,
)
from core.entity import EntityRegistry


def _register(name: str, risk: str = "") -> None:
    meta = {"risk": risk} if risk else {}
    EntityRegistry.register_tool(
        name=name, func=lambda: "ok", description=name,
        group="approval_test", meta=meta,
    )


@pytest.fixture
def critical_tool():
    """注册一个声明 CRITICAL 风险的测试工具（用例结束注销）。"""
    _register("test_meta_critical_tool", risk="CRITICAL")
    yield
    EntityRegistry.unregister("test_meta_critical_tool")


class TestToolMetaRiskRule:
    def test_critical_meta_synthesizes_ask_rule(self, critical_tool):
        rule = tool_meta_risk_rule("test_meta_critical_tool")
        assert rule is not None
        assert rule.effect == PermissionEffect.ASK
        assert rule.risk_level == RiskLevel.CRITICAL
        assert rule.pattern == _META_RISK_PATTERN

    def test_medium_meta_not_promoted(self):
        _register("test_meta_medium_tool", risk="MEDIUM")
        try:
            assert tool_meta_risk_rule("test_meta_medium_tool") is None
        finally:
            EntityRegistry.unregister("test_meta_medium_tool")

    def test_unregistered_tool_returns_none(self):
        assert tool_meta_risk_rule("test_meta_absent_tool") is None

    def test_no_risk_meta_returns_none(self):
        _register("test_meta_plain_tool")
        try:
            assert tool_meta_risk_rule("test_meta_plain_tool") is None
        finally:
            EntityRegistry.unregister("test_meta_plain_tool")


class TestEvaluateMetaTier:
    def test_default_allow_promotes_critical_to_ask(self, critical_tool):
        rs = PermissionRuleSet()
        v = rs.evaluate("test_meta_critical_tool", {}, "", "")
        assert v.decision == PermissionDecision.ASK
        assert v.rule is not None
        assert v.rule.pattern == _META_RISK_PATTERN
        assert v.rule.risk_level == RiskLevel.CRITICAL
        assert "CRITICAL" in v.reason

    def test_explicit_allow_rule_overrides_meta(self, critical_tool):
        rs = PermissionRuleSet(rules=[
            PermissionRule(pattern="test_meta_critical_tool",
                           effect=PermissionEffect.ALLOW),
        ])
        v = rs.evaluate("test_meta_critical_tool", {}, "", "")
        assert v.decision == PermissionDecision.AUTO_ALLOW
        assert v.rule is not None
        assert v.rule.effect == PermissionEffect.ALLOW

    def test_explicit_deny_rule_overrides_meta(self, critical_tool):
        rs = PermissionRuleSet(rules=[
            PermissionRule(pattern="test_meta_critical_tool",
                           effect=PermissionEffect.DENY),
        ])
        assert rs.evaluate("test_meta_critical_tool", {}, "", "").decision \
            == PermissionDecision.AUTO_DENY

    def test_default_ask_unchanged_by_meta(self, critical_tool):
        # 默认本就 ask（比元数据层更严），走默认分支且规则形态不同
        rs = PermissionRuleSet(default_effect=PermissionEffect.ASK)
        v = rs.evaluate("test_meta_critical_tool", {}, "", "")
        assert v.decision == PermissionDecision.ASK
        assert v.rule is not None
        assert v.rule.pattern == "*"

    def test_default_deny_unchanged_by_meta(self, critical_tool):
        rs = PermissionRuleSet(default_effect=PermissionEffect.DENY)
        assert rs.evaluate("test_meta_critical_tool", {}, "", "").decision \
            == PermissionDecision.AUTO_DENY

    def test_plain_tool_still_auto_allow(self):
        _register("test_meta_plain_tool")
        try:
            v = PermissionRuleSet().evaluate("test_meta_plain_tool", {}, "", "")
            assert v.decision == PermissionDecision.AUTO_ALLOW
            assert v.rule is None
        finally:
            EntityRegistry.unregister("test_meta_plain_tool")
