"""run_shell_command 语义测试（cwd 持久/输出落盘/超时）。"""

from __future__ import annotations

import json
import os

import pytest

from entities.filesystem import shell_state, tools


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_load_config", lambda: None)
    monkeypatch.setattr(tools, "_ws_root", lambda: str(tmp_path))
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


class TestMemoryKeyNote:
    """便签索引键（memory/*.md）触碰 shell：事前拦截（键误用/便签树直访）+ 事后键空间归因。"""

    @staticmethod
    def _notes_root(workspace, monkeypatch, files=()):
        from core import path as core_path

        data_dir = workspace / "dataroot" / "memory"
        data_dir.mkdir(parents=True)
        for name in files:
            (data_dir / name).write_text("hb\n", encoding="utf-8")
        monkeypatch.setitem(core_path._PATH_OVERRIDES, "MEMORY_DIR", str(data_dir))
        return data_dir

    def test_key_misuse_blocked_before_execution(self, workspace, monkeypatch):
        """便签树内存在的键在执行前拦截（事故命令 tail -n 50 memory/xxx.md 同款）。"""
        self._notes_root(workspace, monkeypatch, files=["heartbeat.md"])
        result = _run("tail -n 50 memory/heartbeat.md")
        assert result["guard"] == "memory_notes"
        assert result["cause"] == "param" and result["retryable"] is False
        assert "read_memory_file" in result["hint"]
        assert "stdout" not in result  # 未消耗一次执行

    def test_absolute_path_into_notes_root_blocked(self, workspace, monkeypatch):
        data_dir = self._notes_root(workspace, monkeypatch, files=["heartbeat.md"])
        result = _run(f"cat {data_dir / 'heartbeat.md'}")
        assert result["guard"] == "memory_notes"

    def test_background_command_blocked_too(self, workspace, monkeypatch):
        self._notes_root(workspace, monkeypatch, files=["heartbeat.md"])
        result = json.loads(
            tools.run_shell_command("tail memory/heartbeat.md", run_in_background=True)
        )
        assert result["guard"] == "memory_notes"

    def test_note_without_real_file_still_states_namespace(self, workspace, monkeypatch):
        self._notes_root(workspace, monkeypatch)
        result = _run("cat memory/gone.md")
        assert result["ok"] is False
        assert any("便签索引键" in n for n in result.get("notes", []))

    def test_no_note_when_file_exists_in_cwd(self, workspace):
        (workspace / "memory").mkdir()
        (workspace / "memory" / "local.md").write_text("x\n", encoding="utf-8")
        result = _run("cat memory/local.md no_such_file")
        assert result["ok"] is False
        assert not any("便签索引键" in n for n in result.get("notes", []))


class TestMissingModuleHint:
    """uv venv 缺失模块错误（No module named pip/xxx）的环境事实提示。"""

    @staticmethod
    def _uv_managed(monkeypatch):
        from entities.system import python_service
        monkeypatch.setattr(
            python_service, "detect_env_manager",
            lambda p: {"manager": "uv", "uv_managed": True, "uv_version": "0.9"},
        )

    def test_fact_hint_no_commands(self, monkeypatch):
        self._uv_managed(monkeypatch)
        hint = tools._missing_module_hint("x: No module named pip", "")
        assert hint and "不含 pip" in hint
        # 只陈述事实：不出现操作命令
        assert "uv pip install" not in hint and "uv add" not in hint

    def test_quoted_module_variant(self, monkeypatch):
        self._uv_managed(monkeypatch)
        assert tools._missing_module_hint(
            "", "ModuleNotFoundError: No module named 'paramiko'"
        ) is not None

    def test_no_hint_for_non_uv(self, monkeypatch):
        from entities.system import python_service
        monkeypatch.setattr(
            python_service, "detect_env_manager",
            lambda p: {"manager": "pip", "uv_managed": False, "uv_version": None},
        )
        assert tools._missing_module_hint("", "No module named 'x'") is None

    def test_no_hint_without_match(self):
        assert tools._missing_module_hint("all good", "") is None

    def test_note_in_failure_result(self, workspace, monkeypatch):
        self._uv_managed(monkeypatch)
        result = _run('echo "No module named pip" >&2; exit 1')
        assert result["ok"] is False
        assert any("不含 pip" in n for n in result.get("notes", []))
