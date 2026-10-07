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
    from core.path import ConfigPaths

    monkeypatch.setattr(task_tools, "_tasks_dir", lambda: tmp_path / "tasks")
    # save/load 均动态解析 ConfigPaths.HEARTBEAT_CONFIG，经元类 override 整体重定向
    monkeypatch.setattr(ConfigPaths, "HEARTBEAT_CONFIG", str(tmp_path / "heartbeat.json"))
    # TASKS_DIR / TASK_HISTORY 一并隔离：心跳引擎构造/reload 的对账清理
    # （orphan 运行数据）按注册表存活面回写这两处，不隔离则测试会触达真实文件
    monkeypatch.setattr(ConfigPaths, "TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setattr(ConfigPaths, "TASK_HISTORY", str(tmp_path / "task_history.json"))
    monkeypatch.setattr(hb_config, "_instance", None)
    monkeypatch.setattr(hb_log, "_log_path", lambda: tmp_path / "heartbeat.md")
    yield
    monkeypatch.setattr(hb_config, "_instance", None)
