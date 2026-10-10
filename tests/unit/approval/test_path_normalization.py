"""A1 路径规范化防权限绕过测试（执行层与权限层同一解析）。"""

from __future__ import annotations

import os

import pytest

from agent.approval.matching import extract_matchable_arg, matchable_arg_candidates
from agent.approval.rules import PermissionDecision, PermissionEffect, PermissionRule, PermissionRuleSet
from entities.filesystem import paths as fs_paths


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """把 workspace 指向临时目录（配置层与工具层同时生效）。"""
    monkeypatch.setattr(fs_paths, "get_workspace_config", lambda: str(tmp_path))
    monkeypatch.setattr(fs_paths, "get_workspace_root", lambda: str(tmp_path))
    return tmp_path


class TestPathResolution:
    def test_dot_prefix_stripped(self, workspace):
        resolved = fs_paths.resolve_workspace_path("./config/app.json")
        assert resolved == os.path.join(str(workspace), "config", "app.json")

    def test_dotdot_cannot_escape(self, workspace):
        resolved = fs_paths.resolve_workspace_path("config/../../etc/passwd")
        # normpath 归一后漂出 workspace（由沙箱层拦截，解析层如实返回）
        assert ".." not in resolved

    def test_workspace_prefix_dedup(self, workspace):
        name = os.path.basename(str(workspace))
        resolved = fs_paths.resolve_workspace_path(f"{name}/a.txt")
        assert resolved == os.path.join(str(workspace), "a.txt")

    def test_tilde_expanded(self):
        resolved = fs_paths.resolve_workspace_path("~/x.txt")
        assert resolved == os.path.normpath(os.path.expanduser("~/x.txt"))


class TestBypassPrevention:
    @pytest.mark.parametrize("tool", ["copy_file", "move_file"])
    def test_two_paths_with_spaces_deny_either(self, workspace, tool):
        rule = PermissionRule(pattern=f"{tool}(private/**)", effect=PermissionEffect.DENY)
        assert rule.matches(tool, {"src": "public/a b.txt", "dst": "private/c d.txt"}, "", "")
        assert rule.matches(tool, {"src": "private/a b.txt", "dst": "public/c d.txt"}, "", "")
        allow = PermissionRule(pattern=f"{tool}(public/**)", effect=PermissionEffect.ALLOW)
        assert not allow.matches(tool, {"src": "public/a b.txt", "dst": "private/c.txt"}, "", "")
        assert allow.matches(tool, {"src": "public/a b.txt", "dst": "public/c.txt"}, "", "")

    def test_relative_glob_still_matches(self, workspace):
        candidates = matchable_arg_candidates("edit_file", {"file_path": "config/app.json"})
        assert os.path.join("config", "app.json") in candidates

    def test_dot_bypass_caught(self, workspace):
        rs = PermissionRuleSet(rules=[
            PermissionRule(pattern="edit_file(config/**)", effect=PermissionEffect.DENY),
        ])
        v = rs.evaluate("edit_file", {"file_path": "./config/app.json"}, "", "u")
        assert v.decision == PermissionDecision.AUTO_DENY

    def test_dotdot_bypass_caught(self, workspace):
        rs = PermissionRuleSet(rules=[
            PermissionRule(pattern="edit_file(config/**)", effect=PermissionEffect.DENY),
        ])
        v = rs.evaluate("edit_file", {"file_path": "x/../config/app.json"}, "", "u")
        assert v.decision == PermissionDecision.AUTO_DENY

    def test_absolute_glob_matches(self, workspace):
        rs = PermissionRuleSet(rules=[
            PermissionRule(pattern=f"edit_file({workspace}/secret/**)", effect=PermissionEffect.DENY),
        ])
        v = rs.evaluate("edit_file", {"file_path": "secret/key.pem"}, "", "u")
        assert v.decision == PermissionDecision.AUTO_DENY

    def test_tilde_bypass_caught(self, workspace, monkeypatch):
        fake_home = str(workspace / "home")
        monkeypatch.setenv("HOME", fake_home)
        monkeypatch.setenv("USERPROFILE", fake_home)
        rs = PermissionRuleSet(rules=[
            PermissionRule(pattern=f"edit_file({fake_home}/**)", effect=PermissionEffect.DENY),
        ])
        v = rs.evaluate("edit_file", {"file_path": "~/private.txt"}, "", "u")
        assert v.decision == PermissionDecision.AUTO_DENY

    def test_non_path_tools_unaffected(self, workspace):
        assert extract_matchable_arg("run_shell_command", {"command": "ls ./x"}) == "ls ./x"
