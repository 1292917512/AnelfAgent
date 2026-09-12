"""心跳态势区块单元测试：受管区块读写 / 引擎维护内容 / 注入读取器 / reload 即时刷新 / 读工具。"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

import agent.task.history as task_history
from agent.heartbeat.config import HeartbeatConfig, ScheduleMode, TaskSchedule
from agent.heartbeat.engine import HeartbeatEngine
from agent.memory import notes as notes_mod
from agent.task.model import TaskDefinition


@pytest.fixture(autouse=True)
def _isolate_notes_and_history(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """隔离便签工作区与执行历史文件（两者都指向真实数据目录，必须显式重定向）。"""
    ws = tmp_path / "config"
    (ws / "memory").mkdir(parents=True)
    monkeypatch.setattr(notes_mod, "_workspace_dir", ws)
    monkeypatch.setattr(task_history, "_history_path", lambda: tmp_path / "task_history.json")
    yield


def _task(name: str, *, enabled: bool = True) -> TaskDefinition:
    return TaskDefinition(name=name, prompt=f"{name} prompt", enabled=enabled)


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

    def reload(self) -> None:  # pragma: no cover - 引擎 reload 不在测试路径
        pass


def _make_engine(
    config: HeartbeatConfig,
    tasks: dict[str, TaskDefinition],
    monkeypatch: pytest.MonkeyPatch,
) -> HeartbeatEngine:
    monkeypatch.setattr(
        "agent.heartbeat.engine.TaskRegistry", lambda: _FakeRegistry(tasks),
    )
    monkeypatch.setattr("agent.heartbeat.engine.get_heartbeat_config", lambda: config)
    # reload() 内部经 from .config import 就地导入，需在源模块上打桩防读真实 heartbeat.json
    monkeypatch.setattr("agent.heartbeat.config.reload_heartbeat_config", lambda: config)
    monkeypatch.setattr(HeartbeatConfig, "save", lambda self, path=None: None)
    engine = HeartbeatEngine(SimpleNamespace())
    return engine


# ==================================================================
# 受管区块读写
# ==================================================================


class TestManagedBlock:
    def test_update_and_read_round_trip(self) -> None:
        assert notes_mod.update_managed_block("heartbeat-status", "- 心跳：运行中\n") is True
        body = notes_mod.read_managed_block("heartbeat-status")
        assert "心跳：运行中" in body
        # 主便签落盘且位于动态分界之后
        text = notes_mod.get_notes_path().read_text(encoding="utf-8")
        assert text.index("# 当前状态") < text.index(notes_mod.AUTO_HEARTBEAT_BEGIN)

    def test_unchanged_content_no_write(self) -> None:
        notes_mod.update_managed_block("memory-status", "- 内容 A")
        path = notes_mod.get_notes_path()
        first_mtime = path.stat().st_mtime_ns
        assert notes_mod.update_managed_block("memory-status", "- 内容 A") is False
        assert path.stat().st_mtime_ns == first_mtime

    def test_multiple_blocks_coexist_and_strip(self) -> None:
        notes_mod.update_managed_block("memory-status", "- 记忆状态")
        notes_mod.update_managed_block("heartbeat-status", "- 心跳状态")
        text = notes_mod.load_notes_content()
        assert "记忆状态" in text and "心跳状态" in text
        # 动态便签构建剥离全部受管区块（防 context 层双注入）
        dynamic = notes_mod.build_dynamic_notes()
        assert "记忆状态" not in dynamic and "心跳状态" not in dynamic

    def test_heartbeat_reader_header_points_to_tools(self) -> None:
        notes_mod.update_managed_block("heartbeat-status", "## 心跳与任务态势\n- 心跳：运行中")
        block = notes_mod.build_heartbeat_status_block()
        assert block.startswith("[心跳与任务态势]")
        assert "list_tasks" in block and "task_history" in block
        assert "get_heartbeat_log" in block
        # 区块读取器互不串扰
        assert notes_mod.build_memory_status_block() == ""

    def test_write_protection_covers_new_block(self) -> None:
        notes_mod.update_managed_block("heartbeat-status", "- 心跳状态")
        text = notes_mod.load_notes_content()
        with pytest.raises(ValueError, match="heartbeat-status"):
            notes_mod._assert_managed_blocks_intact(text, text.replace("- 心跳状态", "- 被篡改"))


# ==================================================================
# 引擎态势区块内容
# ==================================================================


class TestEngineHeartbeatStatus:
    async def test_block_content_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task_history.record_execution(
            "self_reflection", started_at=time.time() - 7200,
            duration_ms=84000, status="success", trigger="heartbeat",
        )
        task_history.record_execution(
            "broken_task", started_at=time.time() - 600,
            duration_ms=5000, status="error", trigger="scheduled",
        )
        config = HeartbeatConfig(interval_seconds=300, task_schedules=[
            TaskSchedule(task_name="self_reflection", mode=ScheduleMode.HEARTBEAT, every_n_beats=10),
            TaskSchedule(task_name="broken_task", mode=ScheduleMode.SCHEDULED, schedule_times=["04:00"]),
            TaskSchedule(task_name="ghost", mode=ScheduleMode.HEARTBEAT),  # 定义缺失：不渲染
        ])
        engine = _make_engine(config, {
            "self_reflection": _task("self_reflection"),
            "broken_task": _task("broken_task"),
            "manual_only": _task("manual_only", enabled=False),
        }, monkeypatch)

        await engine._write_heartbeat_status()

        body = notes_mod.read_managed_block("heartbeat-status")
        assert "每 300s 一拍" in body
        assert "共 3 个（启用 2）" in body
        assert "调度绑定 2 条" in body
        # 调度节奏折算 + 最近执行概况
        assert "self_reflection（每 10 拍执行（约 50 分钟））" in body
        assert "成功（1m24s）" in body
        assert "broken_task（每日 04:00）" in body
        # 失败任务告警行
        assert "最近执行失败：broken_task" in body
        # 逐拍计数刻意缺席（缓存纪律：任务未执行期间区块字节冻结）
        assert "total_ticks" not in body and "beat_count" not in body

    async def test_stable_when_nothing_changed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无执行无变更时重写返回 False（区块字节不变，缓存零扰动）。"""
        config = HeartbeatConfig(task_schedules=[
            TaskSchedule(task_name="t1", mode=ScheduleMode.HEARTBEAT, every_n_beats=10),
        ])
        engine = _make_engine(config, {"t1": _task("t1")}, monkeypatch)
        await engine._write_heartbeat_status()
        path = notes_mod.get_notes_path()
        first = path.stat().st_mtime_ns
        await engine._write_heartbeat_status()
        assert path.stat().st_mtime_ns == first

    async def test_reload_schedules_refresh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """任务/调度 CRUD 走 reload 热更，态势区块即时刷新（不等下个心跳拍）。"""
        config = HeartbeatConfig()
        engine = _make_engine(config, {}, monkeypatch)
        assert notes_mod.read_managed_block("heartbeat-status") == ""
        engine.reload()
        await asyncio.sleep(0.05)  # 让 create_task 调度出的刷新协程跑完
        assert "心跳" in notes_mod.read_managed_block("heartbeat-status")


# ==================================================================
# AI 读工具：get_heartbeat_log
# ==================================================================


class TestGetHeartbeatLogTool:
    async def test_reads_recent_entries(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent.heartbeat.log as hb_log
        monkeypatch.setattr(hb_log, "LOG_PATH", tmp_path / "heartbeat.md")
        hb_log.write_log(task_names=["demo"], exec_results=["REPLY 成功"])

        from agent.memory.tools import get_heartbeat_log
        raw = await get_heartbeat_log(2)
        data = json.loads(raw)
        assert data["ok"] is True
        assert "REPLY 成功" in data["log"]

    async def test_empty_log_returns_message(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent.heartbeat.log as hb_log
        monkeypatch.setattr(hb_log, "LOG_PATH", tmp_path / "heartbeat.md")

        from agent.memory.tools import get_heartbeat_log
        data = json.loads(await get_heartbeat_log())
        assert data["ok"] is True and data["count"] == 0
