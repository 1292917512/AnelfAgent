"""心跳 idle 调度单元测试：空闲计数 / 选择优先级 / 单例校验 / 反思延迟 / 同任务去重。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.heartbeat.config import (
    HeartbeatConfig,
    ScheduleMode,
    TaskSchedule,
    validate_schedules,
)
from agent.heartbeat.engine import HeartbeatEngine
from agent.memory.memory_types import MemoryType
from agent.task.model import TaskDefinition, TaskResult


def _task(name: str, *, enabled: bool = True) -> TaskDefinition:
    return TaskDefinition(name=name, prompt=f"{name} prompt")


def _result(name: str) -> TaskResult:
    return TaskResult(
        task_name=name, content="产出", memory_type=MemoryType.REFLECTION,
        source=name,
    )


class _FakeRegistry:
    """最小任务注册表替身（避免读取真实 config/tasks）。"""

    def __init__(self, tasks: dict[str, TaskDefinition], file_stems: frozenset[str] = frozenset()) -> None:
        self._tasks = tasks
        self._file_stems = file_stems

    def get(self, name: str):
        return self._tasks.get(name)

    def task_file_exists(self, name: str) -> bool:
        return name in self._file_stems

    def list_all(self):
        return list(self._tasks.values())

    def reload(self) -> None:  # pragma: no cover - 引擎 reload 不在测试路径
        pass


def _make_engine(
    schedules: list[TaskSchedule],
    tasks: dict[str, TaskDefinition],
    monkeypatch: pytest.MonkeyPatch,
    *,
    last_activity: float = 0.0,
    file_stems: frozenset[str] = frozenset(),
    save_calls: list[HeartbeatConfig] | None = None,
) -> tuple[HeartbeatEngine, SimpleNamespace]:
    mind = SimpleNamespace(last_activity_ts=last_activity)
    config = HeartbeatConfig(task_schedules=schedules)

    # 隔离真实配置文件：TaskRegistry / get_heartbeat_config / save 均指向测试替身
    # （save 落盘路径是模块级 _CONFIG_PATH，不隔离会覆盖真实 config/heartbeat.json）
    monkeypatch.setattr(
        "agent.heartbeat.engine.TaskRegistry", lambda: _FakeRegistry(tasks, file_stems),
    )
    monkeypatch.setattr(
        "agent.heartbeat.engine.get_heartbeat_config", lambda: config,
    )
    if save_calls is None:
        monkeypatch.setattr(HeartbeatConfig, "save", lambda self, path=None: None)
    else:
        monkeypatch.setattr(
            HeartbeatConfig, "save", lambda self, path=None: save_calls.append(self),
        )
    engine = HeartbeatEngine(mind)
    # 内置维护与执行器全部替身化：测试只关心调度决策
    engine._run_maintenance = AsyncMock()  # type: ignore[method-assign]
    engine.executor = SimpleNamespace(run=AsyncMock(side_effect=lambda t, e, **k: _result(t.name)))
    return engine, mind


class TestIdleConfig:
    def test_validate_allows_single_idle(self) -> None:
        schedules = [
            TaskSchedule(task_name="a", mode=ScheduleMode.IDLE),
            TaskSchedule(task_name="b", mode=ScheduleMode.HEARTBEAT),
        ]
        assert validate_schedules(schedules) is None

    def test_validate_rejects_multiple_idle(self) -> None:
        schedules = [
            TaskSchedule(task_name="a", mode=ScheduleMode.IDLE),
            TaskSchedule(task_name="b", mode=ScheduleMode.IDLE),
        ]
        err = validate_schedules(schedules)
        assert err is not None and "仅允许一条" in err

    def test_idle_dict_round_trip(self) -> None:
        schedule = TaskSchedule(
            task_name="self_reflection", mode=ScheduleMode.IDLE,
            every_n_beats=4, beat_count=2,
        )
        data = schedule.to_dict()
        assert data["mode"] == "idle"
        assert data["every_n_beats"] == 4
        assert data["beat_count"] == 2
        restored = TaskSchedule.from_dict(data)
        assert restored.mode == ScheduleMode.IDLE
        assert restored.every_n_beats == 4
        assert restored.beat_count == 2


class TestOrphanSchedulePrune:
    def test_prunes_schedule_whose_task_file_deleted(self, monkeypatch) -> None:
        """任务文件已删除的调度在引擎启动时被清理并落盘。"""
        save_calls: list[HeartbeatConfig] = []
        schedules = [
            TaskSchedule(task_name="gone", mode=ScheduleMode.HEARTBEAT, every_n_beats=2),
            TaskSchedule(task_name="alive", mode=ScheduleMode.HEARTBEAT, every_n_beats=2),
        ]
        tasks = {"alive": _task("alive")}
        engine, _ = _make_engine(schedules, tasks, monkeypatch, save_calls=save_calls)
        assert [s.task_name for s in engine.config.task_schedules] == ["alive"]
        assert len(save_calls) == 1

    def test_keeps_schedule_when_task_file_exists_but_unloadable(self, monkeypatch) -> None:
        """任务文件仍在（如 JSON 损坏）时不清理调度，保留 WARN 由人工处理。"""
        save_calls: list[HeartbeatConfig] = []
        schedules = [TaskSchedule(task_name="broken", mode=ScheduleMode.HEARTBEAT, every_n_beats=2)]
        engine, _ = _make_engine(
            schedules, {}, monkeypatch, file_stems=frozenset({"broken"}), save_calls=save_calls,
        )
        assert [s.task_name for s in engine.config.task_schedules] == ["broken"]
        assert save_calls == []

    def test_no_orphans_no_save(self, monkeypatch) -> None:
        save_calls: list[HeartbeatConfig] = []
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=2)]
        engine, _ = _make_engine(schedules, {"a": _task("a")}, monkeypatch, save_calls=save_calls)
        assert [s.task_name for s in engine.config.task_schedules] == ["a"]
        assert save_calls == []


class TestIdleScheduling:
    async def test_counter_accumulates_and_triggers(self, monkeypatch) -> None:
        schedules = [TaskSchedule(task_name="idle_task", mode=ScheduleMode.IDLE, every_n_beats=3)]
        tasks = {"idle_task": _task("idle_task")}
        engine, mind = _make_engine(schedules, tasks, monkeypatch)

        for _ in range(2):
            executed = await engine.tick()
            assert executed == []
        assert schedules[0].beat_count == 2

        executed = await engine.tick()
        assert executed == ["idle_task"]
        # 成功后计数复位
        assert schedules[0].beat_count == 0

    async def test_activity_resets_counter(self, monkeypatch) -> None:
        schedules = [TaskSchedule(task_name="idle_task", mode=ScheduleMode.IDLE, every_n_beats=3)]
        tasks = {"idle_task": _task("idle_task")}
        engine, mind = _make_engine(schedules, tasks, monkeypatch)

        await engine.tick()
        assert schedules[0].beat_count == 1

        # 两次 tick 之间发生真实思考（活动时间戳前移）→ 计数清零
        mind.last_activity_ts = 1000.0
        for _ in range(3):
            await engine.tick()
        assert schedules[0].beat_count == 3 - 1  # 活动当拍清零，其后每拍 +1

    async def test_task_execution_semantics_via_activity(self, monkeypatch) -> None:
        """idle 任务自身执行也会刷新活动（真实路径经 mind.reflect → note_activity）。"""
        schedules = [TaskSchedule(task_name="idle_task", mode=ScheduleMode.IDLE, every_n_beats=1)]
        tasks = {"idle_task": _task("idle_task")}
        engine, mind = _make_engine(schedules, tasks, monkeypatch)

        original_run = engine.executor.run

        async def _run_with_activity(task, entity, **kwargs):
            result = await original_run(task, entity, **kwargs)
            mind.last_activity_ts += 1.0  # 模拟 reflect 入口的 note_activity
            return result

        engine.executor.run = _run_with_activity
        assert await engine.tick() == ["idle_task"]
        # 活动已刷新 → 下一拍不触发（计数归零后重新累计）
        assert await engine.tick() == []

    async def test_scheduled_task_takes_priority_and_freezes_idle(self, monkeypatch) -> None:
        """确定性调度到期优先；idle 计数在让位期间不累计。"""
        schedules = [
            TaskSchedule(task_name="hb_task", mode=ScheduleMode.HEARTBEAT, every_n_beats=1),
            TaskSchedule(task_name="idle_task", mode=ScheduleMode.IDLE, every_n_beats=2),
        ]
        tasks = {"hb_task": _task("hb_task"), "idle_task": _task("idle_task")}
        engine, mind = _make_engine(schedules, tasks, monkeypatch)

        executed = await engine.tick()
        assert executed == ["hb_task"]
        # idle 未被评估（有心跳任务到期），计数保持 0
        assert schedules[1].beat_count == 0

    async def test_pending_reflection_triggers_early_with_note(self, monkeypatch) -> None:
        schedules = [TaskSchedule(task_name="idle_task", mode=ScheduleMode.IDLE, every_n_beats=10)]
        tasks = {"idle_task": _task("idle_task")}
        engine, mind = _make_engine(schedules, tasks, monkeypatch)

        await engine.tick()
        assert schedules[0].beat_count == 1

        engine.mark_reflection_pending("主人提到要跟进 X")
        executed = await engine.tick()
        assert executed == ["idle_task"]
        # 触发原因注入任务指令尾部；消费后清除
        kwargs = engine.executor.run.call_args.kwargs
        assert "主人提到要跟进 X" in kwargs["extra_note"]
        assert engine.reflection_pending is False

    async def test_no_idle_schedule_means_no_pending_trigger(self, monkeypatch) -> None:
        schedules = [TaskSchedule(task_name="hb_task", mode=ScheduleMode.HEARTBEAT, every_n_beats=100)]
        tasks = {"hb_task": _task("hb_task")}
        engine, mind = _make_engine(schedules, tasks, monkeypatch)
        assert engine.has_idle_schedule() is False
        executed = await engine.tick()
        assert executed == []


class TestInflightDedup:
    async def test_run_task_rejects_duplicate_while_running(self, monkeypatch) -> None:
        schedules: list[TaskSchedule] = []
        tasks = {"a": _task("a")}
        engine, _ = _make_engine(schedules, tasks, monkeypatch)

        release = asyncio.Event()

        async def _slow_run(task, entity, **kwargs):
            await release.wait()
            return _result(task.name)

        engine.executor.run = _slow_run
        first = asyncio.create_task(engine.run_task("a"))
        await asyncio.sleep(0)  # 让首任务进入执行（登记 inflight）
        duplicate = await engine.run_task("a")
        assert duplicate is None
        release.set()
        assert (await first) == "产出"

    async def test_tick_skips_inflight_task(self, monkeypatch) -> None:
        schedules = [
            TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=1),
            TaskSchedule(task_name="b", mode=ScheduleMode.HEARTBEAT, every_n_beats=100),
        ]
        tasks = {"a": _task("a"), "b": _task("b")}
        engine, _ = _make_engine(schedules, tasks, monkeypatch)

        # 模拟 a 正被手动触发排队/执行：tick 不得抢跑同任务
        engine._task_inflight.add("a")
        executed = await engine.tick()
        assert executed == []  # a 被跳过，b 未到期

    async def test_tick_inflight_cleared_after_run(self, monkeypatch) -> None:
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=1)]
        tasks = {"a": _task("a")}
        engine, _ = _make_engine(schedules, tasks, monkeypatch)
        await engine.tick()
        assert "a" not in engine._task_inflight

    async def test_tick_failure_keeps_marker_for_retry(self, monkeypatch) -> None:
        schedules = [TaskSchedule(task_name="a", mode=ScheduleMode.HEARTBEAT, every_n_beats=1)]
        tasks = {"a": _task("a")}
        engine, _ = _make_engine(schedules, tasks, monkeypatch)

        async def _boom(task, entity, **kwargs):
            raise RuntimeError("boom")

        engine.executor.run = _boom
        executed = await engine.tick()
        assert executed == []
        # 失败不清零计数（at-least-once），且 inflight 已释放
        assert schedules[0].beat_count >= schedules[0].every_n_beats
        assert "a" not in engine._task_inflight


class TestStartTaskBackground:
    """后台手动触发：校验前置 + 立即返回，不阻塞调用方（AI 工具 execute_task 路径）。"""

    def test_rejects_missing_task(self, monkeypatch) -> None:
        engine, _ = _make_engine([], {}, monkeypatch)
        ok, msg = engine.start_task_background("nope")
        assert ok is False and "不存在" in msg

    def test_rejects_disabled_task(self, monkeypatch) -> None:
        tasks = {"a": TaskDefinition(name="a", prompt="p", enabled=False)}
        engine, _ = _make_engine([], tasks, monkeypatch)
        ok, msg = engine.start_task_background("a")
        assert ok is False and "已禁用" in msg

    def test_rejects_inflight_task(self, monkeypatch) -> None:
        engine, _ = _make_engine([], {"a": _task("a")}, monkeypatch)
        engine._task_inflight.add("a")
        ok, msg = engine.start_task_background("a")
        assert ok is False and "已在执行" in msg

    async def test_accepts_and_schedules_run_task(self, monkeypatch) -> None:
        engine, _ = _make_engine([], {"a": _task("a")}, monkeypatch)
        engine.run_task = AsyncMock(return_value="产出")  # type: ignore[method-assign]
        ok, msg = engine.start_task_background("a")
        assert ok is True and "后台" in msg
        for _ in range(10):
            await asyncio.sleep(0)
            if engine.run_task.await_count:
                break
        engine.run_task.assert_awaited_once_with("a")

    async def test_guarded_swallows_exceptions(self, monkeypatch) -> None:
        engine, _ = _make_engine([], {"a": _task("a")}, monkeypatch)
        engine.run_task = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        await engine._run_task_guarded("a")  # 后台壳吞异常只记日志，不外抛


class TestReflectDeferral:
    async def test_execute_reflect_defers_when_idle_configured(self, monkeypatch) -> None:
        from agent.mind.autonomous import Decision, DecisionType
        from agent.mind.tools.decision_executor import execute_reflect

        schedules = [TaskSchedule(task_name="self_reflection", mode=ScheduleMode.IDLE, every_n_beats=4)]
        tasks = {"self_reflection": _task("self_reflection")}
        engine, _ = _make_engine(schedules, tasks, monkeypatch)
        engine.run_task = AsyncMock(return_value="产出")  # type: ignore[method-assign]

        mind = SimpleNamespace(
            heartbeat_engine=engine,
            _reflecting=False,
            _set_phase=lambda phase: None,
            pfc=SimpleNamespace(has_pending_tasks=lambda: False),
        )
        count = await execute_reflect(mind, Decision(type=DecisionType.REFLECT, reason="对话质量下滑"))
        assert count == 0
        # 未立即执行，仅登记待消费标记
        engine.run_task.assert_not_awaited()
        assert engine.reflection_pending is True
        assert mind._reflecting is False

    async def test_execute_reflect_immediate_without_idle_config(self, monkeypatch) -> None:
        from agent.mind.autonomous import Decision, DecisionType
        from agent.mind.tools.decision_executor import execute_reflect

        schedules = [TaskSchedule(task_name="hb", mode=ScheduleMode.HEARTBEAT, every_n_beats=10)]
        tasks = {"self_reflection": _task("self_reflection"), "hb": _task("hb")}
        engine, _ = _make_engine(schedules, tasks, monkeypatch)
        engine.run_task = AsyncMock(return_value="产出")  # type: ignore[method-assign]

        mind = SimpleNamespace(
            heartbeat_engine=engine,
            _reflecting=False,
            _set_phase=lambda phase: None,
            pfc=SimpleNamespace(has_pending_tasks=lambda: False),
        )
        count = await execute_reflect(mind, Decision(type=DecisionType.REFLECT, reason=""))
        assert count == 1
        engine.run_task.assert_awaited_once_with("self_reflection")
