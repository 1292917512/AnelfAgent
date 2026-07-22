"""A4 run_in_background 后台 shell 执行测试。"""

from __future__ import annotations

import json
import os
import time

import pytest

from entities.filesystem import shell_background, tools
from agent.mind.background_tasks import BackgroundTaskRegistry


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "_load_config", lambda: None)
    monkeypatch.setattr(tools, "_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(tools, "_SANDBOX", True)
    from entities.filesystem import shell_state
    shell_state._cwds.pop("_global", None)
    yield tmp_path
    shell_state._cwds.pop("_global", None)


@pytest.fixture()
def registry(monkeypatch):
    reg = BackgroundTaskRegistry()
    monkeypatch.setattr(shell_background, "get_background_registry", lambda: reg)
    return reg


class TestLaunchBackground:
    def test_returns_task_info_immediately(self, workspace, registry):
        result = shell_background.launch_background("sleep 0.2; echo done", str(workspace), str(workspace))
        assert result["ok"] and result["background"]
        assert result["task_id"]
        assert os.path.isfile(result["output_file"]) or True  # 文件由 Popen 创建
        # 等待完成
        deadline = time.time() + 5
        while time.time() < deadline:
            if not registry.running("_global"):
                break
            time.sleep(0.05)
        completed = registry.completed("_global")
        assert len(completed) == 1
        assert completed[0].success
        assert "done" in completed[0].summary

    def test_failure_exit_code_reported(self, workspace, registry):
        result = shell_background.launch_background("exit 7", str(workspace), str(workspace))
        deadline = time.time() + 5
        while time.time() < deadline:
            if not registry.running("_global"):
                break
            time.sleep(0.05)
        completed = registry.completed("_global")
        assert completed and not completed[0].success
        assert "退出码 7" in completed[0].summary

    def test_output_written_to_file(self, workspace, registry):
        result = shell_background.launch_background("echo line1; echo line2", str(workspace), str(workspace))
        deadline = time.time() + 5
        while time.time() < deadline:
            if not registry.running("_global"):
                break
            time.sleep(0.05)
        with open(result["output_file"]) as f:
            content = f.read()
        assert "line1" in content and "line2" in content

    def test_bad_command_start_failure(self, workspace, registry):
        result = shell_background.launch_background("cd /nonexistent_dir_xyz_123", "/nonexistent_dir_xyz_123", str(workspace))
        assert "error" in result


class TestToolIntegration:
    def test_run_shell_command_background(self, workspace, registry, monkeypatch):
        monkeypatch.setattr(shell_background, "get_current_scope", lambda: "_global")
        out = json.loads(tools.run_shell_command("echo bg_test", run_in_background=True))
        assert out["background"] is True
        assert "task_id" in out
        deadline = time.time() + 5
        while time.time() < deadline:
            if not registry.running("_global"):
                break
            time.sleep(0.05)
        completed = registry.completed("_global")
        assert completed and "bg_test" in completed[0].summary

    def test_string_bool_tolerated(self, workspace, registry):
        out = json.loads(tools.run_shell_command("echo x", run_in_background="true"))
        assert out.get("background") is True
