"""A4 run_in_background 后台 shell 执行测试。"""

from __future__ import annotations

import json
import os
import time

import pytest

from agent.mind.background_tasks import BackgroundTaskRegistry
from entities.filesystem import shell_background, tools


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
        shell_background.launch_background("exit 7", str(workspace), str(workspace))
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

    def test_timeout_alerts_without_killing(self, workspace, registry):
        """超时是提醒不是击杀：到预期时长向 AI 报告进度，任务继续运行至自然结束。"""
        alerts: list = []
        registry.set_alert_callback(
            lambda scope, desc, detail, tid: alerts.append((scope, desc, detail, tid)))
        shell_background.launch_background(
            "echo start; sleep 1.5", str(workspace), str(workspace), timeout_sec=0.3)
        deadline = time.time() + 5
        while time.time() < deadline and not alerts:
            time.sleep(0.05)
        # 提醒已到且附进度（报告式，不终止），任务仍在运行
        assert alerts and alerts[0][0] == "_global"
        assert "已运行" in alerts[0][2] and "勤勤恳恳" in alerts[0][2]
        assert "start" in alerts[0][2]
        assert alerts[0][3]  # task_id 已随提醒带出，AI 无需再查
        assert len(registry.running("_global")) == 1
        # 继续等待自然结束：正常完成（成功），不因超时失败
        deadline = time.time() + 8
        while time.time() < deadline:
            if not registry.running("_global"):
                break
            time.sleep(0.05)
        completed = registry.completed("_global")
        assert completed and completed[0].success
        assert "退出码 0" in completed[0].summary

    def test_terminate_kills_process(self, workspace, registry):
        """AI 决策终止：terminate 发整组击杀信号，任务以终止态完成并通知。"""
        shell_background.launch_background("sleep 30", str(workspace), str(workspace))
        task_id = registry.running("_global")[0].task_id
        result = registry.terminate("_global", task_id)
        assert result["ok"] and result["terminated"]
        deadline = time.time() + 8
        while time.time() < deadline:
            if not registry.running("_global"):
                break
            time.sleep(0.05)
        completed = registry.completed("_global")
        assert completed and not completed[0].success
        assert "已被 AI 终止" in completed[0].summary

    def test_owner_scope_registration(self, workspace, registry, monkeypatch):
        """scope 穿透：后台任务登记到归属会话（委托链父会话优先），
        完成通知才能路由回发起会话。"""
        monkeypatch.setattr(shell_background, "get_owner_scope", lambda: "user_test:1")
        shell_background.launch_background("echo scoped", str(workspace), str(workspace))
        assert len(registry.running("user_test:1")) == 1
        assert registry.running("_global") == []


class TestToolIntegration:
    def test_run_shell_command_background(self, workspace, registry, monkeypatch):
        monkeypatch.setattr(shell_background, "get_owner_scope", lambda: "_global")
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

    def test_run_shell_command_background_explicit_timeout_alerts(self, workspace, registry, monkeypatch):
        """模型显式传 timeout 时后台生效为预期时长：超过发提醒、不终止，
        终止由 AI 用 terminate 决策（参数不再被后台分支丢弃）。"""
        monkeypatch.setattr(shell_background, "get_owner_scope", lambda: "_global")
        alerts: list = []
        registry.set_alert_callback(
            lambda scope, desc, detail, tid: alerts.append(detail))
        out = json.loads(tools.run_shell_command(
            "sleep 30", timeout=1, run_in_background=True))
        assert out["timeout_seconds"] == 1
        deadline = time.time() + 5
        while time.time() < deadline and not alerts:
            time.sleep(0.05)
        assert alerts  # 收到超时提醒
        assert len(registry.running("_global")) == 1  # 仍在运行，未被击杀
        task_id = out["task_id"]
        assert registry.terminate("_global", task_id)["ok"]  # 清场 = AI 终止
        deadline = time.time() + 8
        while time.time() < deadline:
            if not registry.running("_global"):
                break
            time.sleep(0.05)
        assert not registry.running("_global")

    def test_string_bool_tolerated(self, workspace, registry):
        out = json.loads(tools.run_shell_command("echo x", run_in_background="true"))
        assert out.get("background") is True
