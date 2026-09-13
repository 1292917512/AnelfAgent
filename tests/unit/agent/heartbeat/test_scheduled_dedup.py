"""定时调度去重与运行态保护单元测试。

覆盖的回归场景（2026-09-12/13 同日重复追跑事故）：
- 槽位去重以执行历史为唯一事实源——重绑调度/reload 换配置对象/进程重启
  都不会导致重复触发；
- error 记录不算"已跑"（at-least-once 重试语义保留）；
- occurrence 锚点判定：多时刻槽位逐点独立、停机补跑不枚举积压、
  跨午夜补跑不吞今日正当槽位（对齐 dsh resolveEveryOccurrence 语义）；
- 连续失败达上限的定时任务当日放弃、跨日恢复；
- 调度重绑继承节拍计数（定义调整不抹运行态进度）；
- 任务执行期间 reload 换掉 self.config 时，计数复位落在新对象而非孤儿引用；
- 无调度任务的失败计数/放弃日期台账随对账清理。
"""

from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agent.task.history as task_history
from agent.heartbeat.config import HeartbeatConfig, ScheduleMode, TaskSchedule
from agent.heartbeat.engine import HeartbeatEngine
from agent.memory.memory_types import MemoryType
from agent.task.model import TaskDefinition, TaskResult


@pytest.fixture(autouse=True)
def _isolate_task_history(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """隔离执行历史文件（槽位去重的事实源，必须指向临时目录）。"""
    monkeypatch.setattr(task_history, "_history_path", lambda: tmp_path / "task_history.json")
    yield


def _task(name: str, *, enabled: bool = True) -> TaskDefinition:
    return TaskDefinition(name=name, prompt=f"{name} prompt")


def _result(name: str) -> TaskResult:
    return TaskResult(
        task_name=name, content="产出", memory_type=MemoryType.REFLECTION,
        source=name,
    )


class _FakeRegistry:
    """最小任务注册表替身（避免读取真实 config/tasks）。"""

    def __init__(self, tasks: dict[str, TaskDefinition]) -> None:
        self._tasks = tasks

    def get(self, name: str):
        return self._tasks.get(name)

    def task_file_exists(self, name: str) -> bool:
        return name in self._tasks

    def list_all(self):
        return list(self._tasks.values())


# "00:00" 对任何当前时刻都满足 current_minutes >= target，且跨午夜窗口
# （1440 - 0 + current <= interval）不成立——恒为"已过点未补触发"的定时槽
_DUE_TIMES = ["00:00"]


def _make_engine(
    schedules: list[TaskSchedule],
    tasks: dict[str, TaskDefinition],
    monkeypatch: pytest.MonkeyPatch,
) -> HeartbeatEngine:
    mind = SimpleNamespace(last_activity_ts=0.0)
    config = HeartbeatConfig(task_schedules=schedules)
    monkeypatch.setattr(
        "agent.heartbeat.engine.TaskRegistry", lambda: _FakeRegistry(tasks),
    )
    monkeypatch.setattr(
        "agent.heartbeat.engine.get_heartbeat_config", lambda: config,
    )
    monkeypatch.setattr(HeartbeatConfig, "save", lambda self, path=None: None)
    engine = HeartbeatEngine(mind)
    engine._run_maintenance = AsyncMock()  # type: ignore[method-assign]
    engine.executor = SimpleNamespace(run=AsyncMock(side_effect=lambda t, e, **k: _result(t.name)))
    return engine


def _record(name: str, started_at: float, *, status: str = "success", trigger: str = "scheduled") -> None:
    task_history.record_execution(
        name, started_at=started_at, duration_ms=1000, status=status, trigger=trigger,
    )


def _record_now(name: str, *, status: str = "success") -> None:
    _record(name, time.time(), status=status)


class _FrozenDateTime(datetime):
    """冻结 engine 模块的墙钟（occurrence 锚点判定的多时刻/跨午夜场景）。"""

    _now = datetime(2026, 9, 13, 12, 0)

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        return cls._now


@pytest.fixture
def frozen_now(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("agent.heartbeat.engine.datetime", _FrozenDateTime)
    return _FrozenDateTime


class TestScheduledDedupByHistory:
    async def test_ran_today_not_triggered(self, monkeypatch) -> None:
        """执行历史已覆盖该 occurrence → 调度不重复触发（重启/丢态后也不追跑）。"""
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=_DUE_TIMES)]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        _record_now("a")

        assert await engine.tick() == []
        engine.executor.run.assert_not_awaited()

    async def test_completion_recorded_suppresses_next_tick(self, monkeypatch) -> None:
        """任务终态落历史即完成"已跑"记账：下一拍起不再触发，无需调度侧刷盘。"""
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=_DUE_TIMES)]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)

        async def _run_recording(task, entity, **kwargs):
            # 模拟真实执行器终态：历史在 run 返回前已原子落盘
            _record_now(task.name)
            return _result(task.name)

        engine.executor.run = _run_recording
        assert await engine.tick() == ["a"]
        assert await engine.tick() == []

    async def test_error_record_does_not_count_as_ran(self, monkeypatch) -> None:
        """今日只有失败记录 → 保留重试（at-least-once），调度照常触发。"""
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=_DUE_TIMES)]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        _record_now("a", status="error")

        assert await engine.tick() == ["a"]

    async def test_no_output_counts_as_ran(self, monkeypatch) -> None:
        """无产出是正常终态（非失败），同样抑制重复触发。"""
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=_DUE_TIMES)]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        _record_now("a", status="no_output")

        assert await engine.tick() == []

    async def test_manual_run_covers_passed_occurrence(self, monkeypatch) -> None:
        """手动触发走过的完整执行同样落历史——已过点的定时槽不再重复跑。"""
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=_DUE_TIMES)]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        _record("a", time.time(), trigger="manual")

        assert await engine.tick() == []


class TestOccurrenceAnchoring:
    """occurrence 锚点判定：多时刻逐点独立 / 停机补最近一次 / 补跑不吞今日槽。"""

    async def test_multiple_times_are_independent(self, monkeypatch, frozen_now) -> None:
        """09:00 跑过不抑制 11:00——多时刻槽位逐点独立判定。"""
        frozen_now._now = datetime(2026, 9, 13, 12, 0)
        schedules = [TaskSchedule(
            task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=["09:00", "11:00"],
        )]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        # 09:00 槽已跑（09:30 的记录覆盖 09:00 occurrence）
        _record("a", datetime(2026, 9, 13, 9, 30).timestamp())

        assert await engine.tick() == ["a"]  # 11:00 occurrence 未被覆盖 → 到期

        # 11:00 跑过后两个 occurrence 均被覆盖 → 不再触发
        _record("a", datetime(2026, 9, 13, 12, 0).timestamp())
        assert await engine.tick() == []

    async def test_downtime_catches_up_latest_occurrence_only(self, monkeypatch, frozen_now) -> None:
        """停机多日只补最近一次 occurrence（昨/今日各槽），不枚举积压。"""
        frozen_now._now = datetime(2026, 9, 13, 12, 0)
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=["09:00"])]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        # 上次成功执行在 3 天前
        _record("a", datetime(2026, 9, 10, 9, 30).timestamp())

        assert await engine.tick() == ["a"]

    async def test_missed_yesterday_slot_fires_in_cross_midnight_window(self, monkeypatch, frozen_now) -> None:
        """昨夜 23:58 槽未跑：跨午夜窗口内（次日 00:02）补触发。"""
        frozen_now._now = datetime(2026, 9, 13, 0, 2)
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=["23:58"])]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        _record("a", datetime(2026, 9, 12, 23, 0).timestamp())  # 昨夜槽未覆盖

        assert await engine.tick() == ["a"]

    async def test_cross_midnight_catchup_does_not_eat_todays_slot(self, monkeypatch, frozen_now) -> None:
        """00:02 补跑昨夜槽后，今日 23:58 的正当槽位照常触发（时间戳比较非日期）。

        旧 last_run_date 日粒度语义下补跑记录会把今日槽一并抑制——回归场景。
        """
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=["23:58"])]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)

        frozen_now._now = datetime(2026, 9, 13, 0, 2)
        _record("a", datetime(2026, 9, 13, 0, 2).timestamp())  # 补跑昨夜槽落盘

        frozen_now._now = datetime(2026, 9, 13, 23, 59)  # 今日槽到点
        assert await engine.tick() == ["a"]

    async def test_ran_yesterday_late_slot_not_refired_after_midnight(self, monkeypatch, frozen_now) -> None:
        """昨夜 23:59 已跑：次日 00:02 的跨午夜窗口不重复触发。"""
        frozen_now._now = datetime(2026, 9, 13, 0, 2)
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=["23:58"])]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        _record("a", datetime(2026, 9, 12, 23, 59).timestamp())

        assert await engine.tick() == []


class TestGiveupLedger:
    async def test_scheduled_task_gives_up_after_max_failures(self, monkeypatch) -> None:
        """连续失败达上限 → 当日不再重试；跨日（台账日期非今日）自动恢复。"""
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=_DUE_TIMES)]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)

        engine.executor.run = AsyncMock(side_effect=RuntimeError("boom"))
        for _ in range(3):
            assert await engine.tick() == []
        assert engine.executor.run.await_count == 3
        assert engine._task_giveup_dates.get("a") is not None

        # 第 4 拍：台账命中，不再选取
        assert await engine.tick() == []
        assert engine.executor.run.await_count == 3

        # 跨日恢复：台账日期不是今天 → 重新进入调度（失败继续计数并再次放弃）
        engine._task_giveup_dates["a"] = "1999-01-01"
        assert await engine.tick() == []

    async def test_success_clears_failure_state(self, monkeypatch) -> None:
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=_DUE_TIMES)]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        engine._task_failures["a"] = 2
        engine._task_giveup_dates["a"] = "1999-01-01"

        assert await engine.tick() == ["a"]
        assert "a" not in engine._task_failures
        assert "a" not in engine._task_giveup_dates

    def test_stale_ledger_entries_pruned_without_schedule(self, monkeypatch) -> None:
        """无对应调度的失败计数/放弃日期随对账清理（防同名调度重建时误抑制）。"""
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=5)]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)
        engine._task_failures.update({"a": 2, "ghost": 3})
        engine._task_giveup_dates.update({"a": "2026-09-13", "ghost": "2026-09-13"})

        engine._prune_stale_runtime_state()
        assert engine._task_failures == {"a": 2}
        assert engine._task_giveup_dates == {"a": "2026-09-13"}


class TestRebindInheritsRuntimeState:
    def test_same_counter_mode_rebind_keeps_beat_count(self) -> None:
        """重绑调度定义（改间隔/改时间）不抹节拍进度。"""
        cfg = HeartbeatConfig(task_schedules=[
            TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=10, beat_count=7),
        ])
        cfg.set_schedule(TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=20))
        schedule = cfg.get_schedule("a")
        assert schedule is not None
        assert schedule.every_n_beats == 20
        assert schedule.beat_count == 7

    def test_mode_switch_resets_beat_count(self) -> None:
        """定时 → 计数类模式切换：旧语义计数不带入（从 0 起计）。"""
        cfg = HeartbeatConfig(task_schedules=[
            TaskSchedule(task_name="a", mode=ScheduleMode.SCHEDULED, schedule_times=["04:00"]),
        ])
        cfg.set_schedule(TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=5))
        assert cfg.get_schedule("a").beat_count == 0  # type: ignore[union-attr]

    def test_new_schedule_appended(self) -> None:
        cfg = HeartbeatConfig()
        cfg.set_schedule(TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=3))
        assert len(cfg.task_schedules) == 1
        assert cfg.task_schedules[0].beat_count == 0


class TestOrphanSafeReset:
    async def test_reset_lands_on_current_config_after_reload(self, monkeypatch) -> None:
        """执行期间 reload 换掉 self.config：计数复位必须落在新对象上。

        旧实现用执行前缓存的 schedule 引用写复位——reload 后该引用已成孤儿，
        复位丢失导致任务被立即重跑（2026-09-13 05:22 无重启同日重跑实证）。
        """
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=1)]
        engine = _make_engine(schedules, {"a": _task("a")}, monkeypatch)

        reloaded = HeartbeatConfig(task_schedules=[
            TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=1, beat_count=1),
        ])

        async def _run_with_reload(task, entity, **kwargs):
            engine.config = reloaded  # 模拟执行期间的 engine.reload() 换对象
            return _result(task.name)

        engine.executor.run = _run_with_reload
        assert await engine.tick() == ["a"]
        # 复位落在当前（新）配置对象上，而非执行前缓存的孤儿引用
        assert reloaded.task_schedules[0].beat_count == 0
        assert schedules[0].beat_count == 1

