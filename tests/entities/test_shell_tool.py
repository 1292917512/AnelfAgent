"""run_shell_command 对齐 Claude Code Bash 语义的测试（cwd 持久/输出落盘/超时）。"""

from __future__ import annotations

import json
import os

import pytest

from entities.filesystem import shell_state, tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_load_config", lambda: None)
    monkeypatch.setattr(tools, "_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(tools, "_SANDBOX", True)
    shell_state._cwds.pop("_global", None)
    yield tmp_path
    shell_state._cwds.pop("_global", None)


def _run(command: str, timeout: int = 30):
    return json.loads(tools.run_shell_command(command, timeout=timeout))


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell 语义")
class TestShellCwd:
    def test_cwd_persists_across_commands(self, workspace):
        (workspace / "subdir").mkdir()
        assert _run("cd subdir")["ok"]
        result = _run("pwd -P")
        assert result["ok"]
        assert result["stdout"].endswith("subdir")

    def test_drift_outside_workspace_resets(self, workspace):
        _run("cd /tmp")
        result = _run("pwd -P")
        assert os.path.abspath(result["stdout"]) == os.path.abspath(str(workspace))

    def test_reset_note_reported(self, workspace):
        result = _run("cd /tmp")
        assert any("重置" in n for n in result.get("notes", []))

    def test_sandbox_disabled_allows_drift(self, workspace, monkeypatch):
        monkeypatch.setattr(tools, "_SANDBOX", False)
        _run("cd /tmp")
        result = _run("pwd -P")
        assert result["stdout"] == "/tmp" or result["stdout"].startswith("/private/tmp")

    def test_exit_code_preserved(self, workspace):
        result = _run("exit 3")
        assert result["ok"] is False

    def test_stderr_captured(self, workspace):
        result = _run("echo oops 1>&2")
        assert "oops" in result["stderr"]


class TestOutputPersistence:
    def test_small_output_inline(self, workspace):
        result = _run("echo hello")
        assert result["stdout"] == "hello"
        assert "persisted" not in result

    def test_large_output_persisted(self, workspace):
        result = _run("seq 1 200000")
        assert "persisted" in result
        assert "<persisted-output>" in result["stdout"]
        path = result["persisted"]
        assert os.path.isfile(path)
        with open(path) as f:
            full = f.read()
        assert "200000" in full
        assert len(result["stdout"]) < shell_state.MAX_OUTPUT_CHARS + 500

    def test_timeout_clamped(self, workspace):
        # 不真正触发超时，只验证钳制不报错
        result = _run("echo ok", timeout=99999)
        assert result["ok"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell 语义")
class TestRedundantWorkspacePrefix:
    def test_note_on_redundant_prefix(self, workspace):
        result = _run(f"ls {workspace.name}/nope")
        assert result["ok"] is False
        assert any("前缀多余" in n for n in result.get("notes", []))

    def test_no_note_without_prefix(self, workspace):
        result = _run("ls nope_such_dir")
        assert result["ok"] is False
        assert not any("前缀多余" in n for n in result.get("notes", []))


class TestRedundantWorkspacePrefixHelper:
    def test_hit(self, workspace):
        assert tools._redundant_workspace_prefix(f"ls {workspace.name}/x") == f"{workspace.name}/x"

    def test_quoted_path(self, workspace):
        cmd = f"ls '{workspace.name}/a b'"
        assert tools._redundant_workspace_prefix(cmd) == f"{workspace.name}/a b"

    def test_miss(self, workspace):
        assert tools._redundant_workspace_prefix("ls x") is None
        # 同名前缀但非路径（无斜杠）不误报
        assert tools._redundant_workspace_prefix(f"cat {workspace.name}_notes.md") is None

    def test_double_prefix_suggests_dot(self, workspace):
        result = _run(f"ls {workspace.name}/{workspace.name}/")
        assert result["ok"] is False
        assert any("直接写 . 即可" in n for n in result.get("notes", []))
