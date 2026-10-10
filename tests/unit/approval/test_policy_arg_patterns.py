"""审批策略参数模式匹配测试（"工具名(参数glob)" 规则）。"""

from __future__ import annotations

from agent.approval.matching import extract_matchable_arg
from agent.approval.rules import PermissionEffect, PermissionRule


def _policy(pattern: str, requires: bool = True) -> PermissionRule:
    return PermissionRule(pattern=pattern, effect=PermissionEffect.ASK if requires else PermissionEffect.ALLOW)


class TestArgPatternParsing:
    def test_split_with_arg_pattern(self):
        p = _policy("run_shell_command(npm test*)")
        assert p._split_pattern() == ("run_shell_command", "npm test*")

    def test_split_plain_pattern(self):
        assert _policy("shell.*")._split_pattern() == ("shell.*", "")


class TestExtractMatchableArg:
    def test_shell_command(self):
        assert extract_matchable_arg("run_shell_command", {"command": "npm test"}) == "npm test"

    def test_edit_file_path(self):
        # A1 起路径参数规范化为绝对路径（防绕过），相对匹配见 matchable_arg_candidates
        import os
        out = extract_matchable_arg("edit_file", {"file_path": "a.py"})
        assert os.path.isabs(out) and out.endswith("a.py")

    def test_move_file_two_paths(self):
        out = extract_matchable_arg("move_file", {"src": "a", "dst": "b"})
        parts = out.split(" ")
        from pathlib import Path
        assert len(parts) == 2 and Path(parts[0]).name == "a" and Path(parts[1]).name == "b"

    def test_unknown_tool_falls_back_to_json(self):
        out = extract_matchable_arg("some_tool", {"x": 1})
        assert '"x": 1' in out


class TestPolicyMatching:
    def test_arg_pattern_hit(self):
        p = _policy("run_shell_command(npm test*)")
        assert p.matches("run_shell_command", {"command": "npm test --watch"})

    def test_arg_pattern_miss(self):
        p = _policy("run_shell_command(npm test*)")
        assert not p.matches("run_shell_command", {"command": "rm -rf /"})

    def test_arg_pattern_without_args_fail_closed(self):
        p = _policy("run_shell_command(npm test*)")
        assert not p.matches("run_shell_command", None)

    def test_tool_name_glob_with_arg_pattern(self):
        p = _policy("edit_file(config/**)")
        assert p.matches("edit_file", {"file_path": "config/app.json"})
        assert not p.matches("edit_file", {"file_path": "src/main.py"})

    def test_plain_glob_unaffected(self):
        assert _policy("shell.*").matches("shell.exec")
        assert not _policy("shell.*").matches("other.exec")
