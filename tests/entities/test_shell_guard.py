"""B6 shell 沙箱预检测试（启发式拦截 workspace 外写操作）。"""

from __future__ import annotations

import json
import os

import pytest

from entities.filesystem import tools
from entities.filesystem.shell_guard import check_command_safety


WS = "/data/workspace"


class TestCheckCommandSafety:
    @pytest.mark.parametrize("cmd", [
        "echo x > /etc/passwd",
        "cat data >> /root/out.txt",
        "rm -rf /var/log",
        "cp a.txt /home/user/",
        "mv /etc/hosts .",
        "tee /usr/local/bin/x",
        "mkdir /opt/evil",
        "chmod 777 /etc/shadow",
    ])
    def test_outside_writes_blocked(self, cmd):
        assert check_command_safety(cmd, WS) is not None

    @pytest.mark.parametrize("cmd", [
        "ls /etc",                              # 读操作放行
        "cat /var/log/syslog | head",           # 读管道放行
        "echo x > /tmp/f.txt",                  # /tmp 良性
        "echo x > /dev/null",                   # /dev/null 良性
        f"echo x > {WS}/out.txt",               # workspace 内放行
        "echo hello",                           # 无路径
        "grep -r pattern /etc/nginx",           # grep 非写动词
        "git status && npm test",               # 无写路径
        "echo ok >&2",                          # 文件描述符复制非重定向
    ])
    def test_benign_commands_allowed(self, cmd):
        assert check_command_safety(cmd, WS) is None

    def test_relative_redirect_allowed(self):
        assert check_command_safety("echo x > local.txt", WS) is None


class TestToolIntegration:
    @pytest.fixture()
    def workspace(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tools, "_load_config", lambda: None)
        monkeypatch.setattr(tools, "_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(tools, "_SANDBOX", True)
        yield tmp_path

    def test_violation_returns_clear_error(self, workspace):
        out = json.loads(tools.run_shell_command("echo x > /etc/evil.conf"))
        assert out["sandbox_violation"] is True
        assert "沙箱拦截" in out["error"]
        assert "/etc/evil.conf" in out["error"]

    def test_normal_command_unaffected(self, workspace):
        out = json.loads(tools.run_shell_command("echo hello"))
        assert out["ok"] and out["stdout"] == "hello"

    def test_check_disabled_by_config(self, workspace, monkeypatch):
        monkeypatch.setattr(tools, "_shell_write_check_enabled", lambda: False)
        out = json.loads(tools.run_shell_command("echo x > /tmp/y.txt && cat /tmp/y.txt"))
        assert out["ok"]
