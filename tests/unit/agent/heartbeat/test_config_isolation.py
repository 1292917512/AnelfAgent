"""心跳配置持久化隔离回归测试（2026-09-12 调度脱落事故根因）。

事故：_CONFIG_PATH 模块常量在导入时冻结，与动态 ConfigPaths 分裂——
load 读隔离路径、save 仍写真实 config/heartbeat.json，测试在真实配置上
反复 CRUD 打掉线上 16 条调度。修复后 save/load 每次动态同源解析，
且文件缺失的迁移分支零副作用。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.heartbeat.config import (
    HeartbeatConfig,
    ScheduleMode,
    TaskSchedule,
)


@pytest.fixture
def _isolate_paths(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from core.path import ConfigPaths

    hb_path = tmp_path / "heartbeat.json"
    monkeypatch.setattr(ConfigPaths, "HEARTBEAT_CONFIG", str(hb_path))
    monkeypatch.setattr(
        ConfigPaths, "INTROSPECTION_CONFIG",
        str(tmp_path / "introspection.json"))
    monkeypatch.setattr(
        ConfigPaths, "INTROSPECTION_DIR", str(tmp_path / "introspection"))
    return hb_path


class TestZeroSideEffectLoad:
    def test_load_missing_file_creates_nothing(self, _isolate_paths) -> None:
        """配置文件缺失：load 只返回内存初始配置，不产生任何磁盘写入。"""
        cfg = HeartbeatConfig.load()
        assert cfg.task_schedules == []
        assert not _isolate_paths.exists()

    def test_load_failure_returns_default_without_save(
            self, _isolate_paths) -> None:
        """文件损坏：返回默认配置，同样不落盘掩盖故障。"""
        _isolate_paths.write_text("{损坏的 json", encoding="utf-8")
        cfg = HeartbeatConfig.load()
        assert cfg.task_schedules == []
        # 原损坏文件原样保留（供人工排查），未被默认档覆盖
        assert "损坏" in _isolate_paths.read_text("utf-8")


class TestDynamicPathSameSource:
    def test_save_load_roundtrip_via_defaults(self, _isolate_paths) -> None:
        """save/load 默认路径同源（都动态解析 ConfigPaths）。

        隔离 ConfigPaths 后 save 必须落到隔离路径——回归钉死
        "load 读 A、save 写 B" 的分裂不再复发。
        """
        cfg = HeartbeatConfig(task_schedules=[
            TaskSchedule(
                task_name="demo", mode=ScheduleMode.HEARTBEAT, every_n_beats=7,
            ),
        ])
        cfg.save()
        assert _isolate_paths.exists()

        loaded = HeartbeatConfig.load()
        sched = loaded.get_schedule("demo")
        assert sched is not None and sched.every_n_beats == 7
