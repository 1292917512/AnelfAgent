"""任务生效时间（expires_at）单元测试：解析 / 过期判定 / 工具语义 / 引擎自动停用。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import agent.task.tools as task_tools
from agent.task.model import (
    TaskDefinition,
    format_task_time,
    normalize_task_time,
    parse_task_time,
)


def _future(days: int = 7) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _past(days: int = 7) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


# ==================================================================
# 时间解析与过期判定
# ==================================================================


class TestTaskTimeParsing:
    def test_date_only_means_end_of_day(self) -> None:
        ts = parse_task_time("2099-03-05")
        assert ts is not None
        dt = datetime.fromtimestamp(ts)
        assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)

    def test_datetime_format(self) -> None:
        ts = parse_task_time("2099-03-05 08:30")
        assert ts is not None
        dt = datetime.fromtimestamp(ts)
        assert (dt.hour, dt.minute) == (8, 30)

    def test_invalid_returns_none(self) -> None:
        assert parse_task_time("not a date") is None
        assert parse_task_time("") is None
        assert parse_task_time(None) is None
        assert parse_task_time("2099-13-40") is None

    def test_normalize_round_trip(self) -> None:
        assert normalize_task_time("2099-03-05") == "2099-03-05"
        assert normalize_task_time("2099-03-05 08:30") == "2099-03-05 08:30"
        assert normalize_task_time("garbage") == ""

    def test_format_task_time(self) -> None:
        assert format_task_time(0.0) == ""
        ts = parse_task_time("2099-03-05 08:30")
        assert ts is not None
        assert format_task_time(ts) == "2099-03-05 08:30"


class TestIsExpired:
    def test_empty_never_expires(self) -> None:
        assert TaskDefinition(name="t", prompt="p").is_expired() is False

    def test_past_expired_future_not(self) -> None:
        expired = TaskDefinition(name="t", prompt="p", expires_at=_past())
        assert expired.is_expired() is True
        alive = TaskDefinition(name="t", prompt="p", expires_at=_future())
        assert alive.is_expired() is False

    def test_date_only_valid_through_the_day(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        task = TaskDefinition(name="t", prompt="p", expires_at=today)
        assert task.is_expired() is False  # 当天 23:59:59 才到期

    def test_invalid_expiry_treated_as_permanent(self) -> None:
        task = TaskDefinition(name="t", prompt="p", expires_at="garbage")
        assert task.is_expired() is False


class TestModelRoundTrip:
    def test_from_dict_tolerates_and_normalizes(self) -> None:
        task = TaskDefinition.from_dict({
            "name": "t", "prompt": "p",
            "expires_at": "2099-03-05", "created_at": 123.0, "updated_at": "bad",
        })
        assert task.expires_at == "2099-03-05"
        assert task.created_at == 123.0
        assert task.updated_at == 0.0  # 非法时间戳容错为 0

    def test_from_dict_drops_invalid_expiry(self) -> None:
        task = TaskDefinition.from_dict({"name": "t", "prompt": "p", "expires_at": "??"})
        assert task.expires_at == ""

    def test_to_dict_emits_only_when_set(self) -> None:
        bare = TaskDefinition(name="t", prompt="p").to_dict()
        assert "expires_at" not in bare and "created_at" not in bare
        full = TaskDefinition(
            name="t", prompt="p", expires_at="2099-03-05",
            created_at=1.0, updated_at=2.0,
        ).to_dict()
        assert full["expires_at"] == "2099-03-05"
        assert full["created_at"] == 1.0 and full["updated_at"] == 2.0
        # 往返无损
        restored = TaskDefinition.from_dict(full)
        assert restored.expires_at == "2099-03-05"


# ==================================================================
# AI 工具语义（create/update）
# ==================================================================


class TestTaskToolExpiry:
    async def test_create_with_expiry(self) -> None:
        raw = await task_tools.create_task("temp_task", "临时任务", expires_at=_future(3))
        data = json.loads(raw)
        assert data["ok"] is True and data["expires_at"]

        saved = json.loads(task_tools._task_path("temp_task").read_text("utf-8"))
        assert saved["expires_at"] == data["expires_at"]
        assert saved["created_at"] > 0

    async def test_create_rejects_invalid_and_past_expiry(self) -> None:
        raw = await task_tools.create_task("bad1", "x", expires_at="not-a-date")
        assert "error" in json.loads(raw)
        raw = await task_tools.create_task("bad2", "x", expires_at=_past())
        assert "error" in json.loads(raw)

    async def test_update_expiry_set_and_clear(self) -> None:
        await task_tools.create_task("t_exp", "做点事")

        raw = await task_tools.update_task("t_exp", expires_at=_future(10))
        data = json.loads(raw)
        assert data["ok"] is True and "expires_at" in data["changed"]
        saved = json.loads(task_tools._task_path("t_exp").read_text("utf-8"))
        assert saved["expires_at"]

        # clear 恢复永久有效（字段移除）
        raw = await task_tools.update_task("t_exp", expires_at="clear")
        data = json.loads(raw)
        assert data["ok"] is True
        saved = json.loads(task_tools._task_path("t_exp").read_text("utf-8"))
        assert "expires_at" not in saved

        # 空串不变；非法/过去时间拒绝
        raw = await task_tools.update_task("t_exp", expires_at=_past())
        assert "error" in json.loads(raw)


# ==================================================================
# 注册表回写与 list_info
# ==================================================================


class TestRegistryUpdateFields:
    async def test_update_task_fields_writes_and_syncs(self, tmp_path) -> None:
        from agent.task.registry import TaskRegistry

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "t1.json").write_text(json.dumps({
            "name": "t1", "prompt": "p", "enabled": True,
        }), "utf-8")
        registry = TaskRegistry(tasks_dir)

        ok = await registry.update_task_fields("t1", {"enabled": False, "updated_at": 1.5})
        assert ok is True
        saved = json.loads((tasks_dir / "t1.json").read_text("utf-8"))
        assert saved["enabled"] is False
        # 内存态同步
        task = registry.get("t1")
        assert task is not None and task.enabled is False

        # 不存在的任务
        assert await registry.update_task_fields("ghost", {"enabled": False}) is False

    def test_list_info_exposes_time_fields(self, tmp_path) -> None:
        from agent.task.registry import TaskRegistry

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "t1.json").write_text(json.dumps({
            "name": "t1", "prompt": "p",
            "expires_at": "2099-01-01", "created_at": 1.0, "updated_at": 2.0,
        }), "utf-8")
        info = TaskRegistry(tasks_dir).list_info()
        assert info[0]["expires_at"] == "2099-01-01"
        assert info[0]["created_at"] == 1.0 and info[0]["updated_at"] == 2.0


# ==================================================================
# 引擎过期自动停用
# ==================================================================


class TestEngineDisableExpired:
    async def test_expired_task_disabled_and_schedule_removed(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agent.heartbeat.config import HeartbeatConfig, ScheduleMode, TaskSchedule
        from agent.heartbeat.engine import HeartbeatEngine
        from agent.task.registry import TaskRegistry

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "old_task.json").write_text(json.dumps({
            "name": "old_task", "prompt": "p", "enabled": True, "expires_at": _past(),
        }), "utf-8")
        (tasks_dir / "live_task.json").write_text(json.dumps({
            "name": "live_task", "prompt": "p", "enabled": True, "expires_at": _future(),
        }), "utf-8")
        (tasks_dir / "permanent.json").write_text(json.dumps({
            "name": "permanent", "prompt": "p", "enabled": True,
        }), "utf-8")

        config = HeartbeatConfig(task_schedules=[
            TaskSchedule(task_name="old_task", mode=ScheduleMode.HEARTBEAT, every_n_beats=2),
            TaskSchedule(task_name="live_task", mode=ScheduleMode.HEARTBEAT, every_n_beats=2),
        ])
        save_calls: list[HeartbeatConfig] = []
        monkeypatch.setattr(
            "agent.heartbeat.engine.TaskRegistry", lambda: TaskRegistry(tasks_dir),
        )
        monkeypatch.setattr(
            "agent.heartbeat.engine.get_heartbeat_config", lambda: config,
        )
        monkeypatch.setattr(
            HeartbeatConfig, "save", lambda self, path=None: save_calls.append(self),
        )

        engine = HeartbeatEngine(SimpleNamespace())
        await engine._disable_expired_tasks()

        # 过期任务：文件停用 + 内存停用 + 调度移除 + updated_at 刷新
        saved = json.loads((tasks_dir / "old_task.json").read_text("utf-8"))
        assert saved["enabled"] is False
        assert saved["updated_at"] > 0
        task = engine.task_registry.get("old_task")
        assert task is not None and task.enabled is False
        assert config.get_schedule("old_task") is None
        assert save_calls  # 调度移除已落盘

        # 未过期与永久任务不受影响
        assert engine.task_registry.get("live_task").enabled is True  # type: ignore[union-attr]
        assert engine.task_registry.get("permanent").enabled is True  # type: ignore[union-attr]
        assert config.get_schedule("live_task") is not None

    async def test_no_expired_tasks_is_noop(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agent.heartbeat.config import HeartbeatConfig, ScheduleMode, TaskSchedule
        from agent.heartbeat.engine import HeartbeatEngine
        from agent.task.registry import TaskRegistry

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "live.json").write_text(json.dumps({
            "name": "live", "prompt": "p", "enabled": True, "expires_at": _future(),
        }), "utf-8")
        config = HeartbeatConfig(task_schedules=[
            TaskSchedule(task_name="live", mode=ScheduleMode.HEARTBEAT),
        ])
        monkeypatch.setattr(
            "agent.heartbeat.engine.TaskRegistry", lambda: TaskRegistry(tasks_dir),
        )
        monkeypatch.setattr("agent.heartbeat.engine.get_heartbeat_config", lambda: config)
        monkeypatch.setattr(HeartbeatConfig, "save", lambda self, path=None: None)

        engine = HeartbeatEngine(SimpleNamespace())
        await engine._disable_expired_tasks()
        assert engine.task_registry.get("live").enabled is True  # type: ignore[union-attr]
        assert config.get_schedule("live") is not None


# ==================================================================
# 执行期元信息注入
# ==================================================================


class TestTaskMetaLines:
    def test_meta_lines_rendered(self) -> None:
        from agent.task.executor import TaskExecutor

        task = TaskDefinition(
            name="t", prompt="p",
            expires_at="2099-03-05", created_at=time.time(), updated_at=time.time(),
        )
        text = TaskExecutor._build_task_meta_lines(task)
        assert "[任务创建]" in text
        assert "[最近更新]" in text
        assert "[生效截止] 2099-03-05" in text

    def test_meta_lines_empty_without_fields(self) -> None:
        from agent.task.executor import TaskExecutor

        assert TaskExecutor._build_task_meta_lines(TaskDefinition(name="t", prompt="p")) == ""

    def test_suffix_contains_self_check_rule(self) -> None:
        from agent.task.executor import TaskExecutor

        suffix = TaskExecutor._build_task_suffix(False)
        assert "任务自检" in suffix and "update_task" in suffix and "delete_task" in suffix
        # handoff 规则顺延为第 6 条
        suffix_h = TaskExecutor._build_task_suffix(False, handoff=True)
        assert "\n6. 本任务为多轮接力任务" in suffix_h
