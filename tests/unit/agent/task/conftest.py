"""agent/task 测试共享 fixture：任务目录 / 心跳配置 / 心跳日志路径隔离。"""

from __future__ import annotations

import pytest

import agent.task.tools as task_tools


@pytest.fixture(autouse=True)
def _isolate_task_and_heartbeat_paths(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """隔离任务定义目录与心跳配置/日志文件（conftest 只隔离 ConfigManager，
    ConfigPaths 解析的是真实 config/，必须显式重定向到 tmp）。"""
    import agent.heartbeat.config as hb_config
    import agent.heartbeat.log as hb_log

    monkeypatch.setattr(task_tools, "_tasks_dir", lambda: tmp_path / "tasks")
    monkeypatch.setattr(hb_config, "_CONFIG_PATH", tmp_path / "heartbeat.json")
    monkeypatch.setattr(hb_config, "_instance", None)
    monkeypatch.setattr(hb_log, "LOG_PATH", tmp_path / "heartbeat.md")
    yield
    monkeypatch.setattr(hb_config, "_instance", None)
