"""agent/heartbeat 测试共享 fixture：心跳配置文件目录级隔离兜底。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_heartbeat_config(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """所有心跳测试默认把 heartbeat.json 重定向到临时目录（autouse 防呆底线）。

    2026-09-12 调度脱落事故：seed 类测试在零隔离替身上触发 config.save()，
    把单条种子档写进真实 config/heartbeat.json，打掉线上 16 条调度。
    目录级兜底后，任何测试无论自身是否隔离，都结构性无法触达真实文件
    （save/load 均动态解析 ConfigPaths.HEARTBEAT_CONFIG，元类 override 生效）。

    TASKS_DIR / TASK_HISTORY 一并隔离：引擎构造与 reload 的对账清理
    （孤儿调度/运行数据残留）会按注册表存活面回写这两处，不隔离则
    替身注册表的小存活集会把真实任务历史/交接文件误判为残留清除。
    """
    from core.path import ConfigPaths

    monkeypatch.setattr(
        ConfigPaths, "HEARTBEAT_CONFIG", str(tmp_path / "heartbeat.json"))
    monkeypatch.setattr(ConfigPaths, "TASKS_DIR", str(tmp_path / "tasks"))
    monkeypatch.setattr(
        ConfigPaths, "TASK_HISTORY", str(tmp_path / "task_history.json"))
    yield
