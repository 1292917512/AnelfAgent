"""心跳配置持久化隔离回归测试（2026-09-12 调度脱落事故根因）。

事故：_CONFIG_PATH 模块常量在导入时冻结，与动态 ConfigPaths 分裂——
load 读隔离路径、save 仍写真实 config/heartbeat.json，测试在真实配置上
反复 CRUD 打掉线上 16 条调度。修复后 save/load 每次动态同源解析，
且文件缺失的迁移分支零副作用。
"""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("mode", ["idle", "heartbeat", "scheduled"])
def test_reflection_migration_preserves_other_schedules_and_model(_isolate_paths, mode):
    raw = {"enabled": False, "task_schedules": [
        {"task_name": "self_reflection", "mode": mode, "every_n_beats": 12,
         "model_id": "chosen", "reasoning_effort": "high", "schedule_times": ["01:00"]},
        {"task_name": "custom_idle", "mode": "idle", "every_n_beats": 4, "beat_count": 2},
        {"task_name": "daily", "mode": "scheduled", "schedule_times": ["05:00"]},
    ]}
    _isolate_paths.write_text(json.dumps(raw), encoding="utf-8")
    cfg = HeartbeatConfig.load()
    reflection = cfg.get_schedule("self_reflection")
    assert reflection.mode == ScheduleMode.MANUAL
    assert reflection.model_id == "chosen" and reflection.reasoning_effort == "high"
    assert cfg.get_schedule("custom_idle").beat_count == 2
    assert cfg.get_schedule("daily").schedule_times == ["05:00"]
    assert not cfg.enabled
    saved = _isolate_paths.read_bytes()
    assert HeartbeatConfig.load().to_dict() == cfg.to_dict()
    assert _isolate_paths.read_bytes() == saved
    from agent.heartbeat.config import validate_schedules
    assert validate_schedules([TaskSchedule("self_reflection", mode=ScheduleMode(mode))])
    with pytest.raises(ValueError, match="按需"):
        cfg.set_schedule(TaskSchedule("self_reflection", mode=ScheduleMode(mode)))


def test_failed_migration_save_keeps_loaded_config(_isolate_paths, monkeypatch):
    _isolate_paths.write_text(json.dumps({"task_schedules": [
        {"task_name": "self_reflection", "mode": "idle"},
        {"task_name": "daily", "mode": "scheduled", "schedule_times": ["05:00"]},
    ]}), encoding="utf-8")
    original = _isolate_paths.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("read only")
    monkeypatch.setattr(HeartbeatConfig, "save", fail)
    cfg = HeartbeatConfig.load()
    assert cfg.get_schedule("self_reflection").mode == ScheduleMode.MANUAL
    assert cfg.get_schedule("daily").schedule_times == ["05:00"]
    assert _isolate_paths.read_bytes() == original
