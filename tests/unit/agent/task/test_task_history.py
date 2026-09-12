"""任务执行历史单元测试：存储读写 / 上限裁剪 / 摘要 / 清理 / 执行器终态记录。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import agent.task.history as task_history
from agent.task.executor import TaskExecutor
from agent.task.model import TaskDefinition


@pytest.fixture(autouse=True)
def _isolate_history_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """隔离执行历史文件（ConfigPaths 解析真实 data 目录，必须显式重定向）。"""
    monkeypatch.setattr(task_history, "_history_path", lambda: tmp_path / "task_history.json")
    yield


def _record(name: str, started_at: float, *, status: str = "success", trigger: str = "manual") -> bool:
    return task_history.record_execution(
        name, started_at=started_at, duration_ms=1000,
        status=status, trigger=trigger, preview=f"产出 {started_at}",
    )


class TestHistoryStore:
    def test_record_and_get_newest_first(self) -> None:
        assert _record("t1", 100.0)
        assert _record("t1", 200.0)
        assert _record("t1", 300.0)

        records = task_history.get_history("t1")
        assert [r["started_at"] for r in records] == [300.0, 200.0, 100.0]
        first = records[0]
        assert first["task_name"] == "t1"
        assert first["duration_ms"] == 1000
        assert first["status"] == "success"
        assert first["trigger"] == "manual"

    def test_get_history_missing_task(self) -> None:
        assert task_history.get_history("ghost") == []

    def test_cap_trims_oldest(self) -> None:
        # 默认上限 5（ConfigManager 已隔离，读到注册默认值）
        for i in range(7):
            assert _record("t2", float(i))
        records = task_history.get_history("t2")
        assert len(records) == 5
        # 保留最近 5 条（新→旧），最老两条被裁剪
        assert [r["started_at"] for r in records] == [6.0, 5.0, 4.0, 3.0, 2.0]

    def test_record_normalizes_fields(self) -> None:
        task_history.record_execution(
            "t3", started_at=1.0, duration_ms=-5, status="bogus",
            trigger="bogus", preview="  x" * 500, error="e" * 500,
        )
        rec = task_history.get_history("t3")[0]
        assert rec["duration_ms"] == 0
        assert rec["status"] == "error"  # 非法状态归一为 error
        assert rec["trigger"] == "manual"  # 非法触发来源归一为 manual
        assert len(rec["preview"]) <= 200
        assert len(rec["error"]) <= 300

    def test_summary_reflects_last_run(self) -> None:
        _record("t4", 10.0, status="error", trigger="heartbeat")
        _record("t4", 20.0, status="no_output", trigger="idle")

        summary = task_history.get_summary()
        assert set(summary) == {"t4"}
        last = summary["t4"]
        assert last["started_at"] == 20.0
        assert last["status"] == "no_output"
        assert last["trigger"] == "idle"
        assert last["duration_ms"] == 1000

    def test_clear_history(self) -> None:
        _record("t5", 1.0)
        assert task_history.clear_history("t5") is True
        assert task_history.get_history("t5") == []
        assert "t5" not in task_history.get_summary()
        # 再次清理：无记录返回 False
        assert task_history.clear_history("t5") is False

    def test_corrupt_file_treated_as_empty_then_self_heals(self, tmp_path) -> None:
        path = tmp_path / "task_history.json"
        path.write_text("{not json", encoding="utf-8")
        assert task_history.get_history("t6") == []

        assert _record("t6", 1.0)
        assert len(task_history.get_history("t6")) == 1

    def test_persistence_round_trip(self, tmp_path) -> None:
        _record("t7", 42.0)
        raw = json.loads((tmp_path / "task_history.json").read_text("utf-8"))
        assert raw["t7"][0]["started_at"] == 42.0


def _fake_mind(reflect_return: str = "", reflect_exc: Exception | None = None) -> SimpleNamespace:
    reflect = AsyncMock()
    if reflect_exc is not None:
        reflect.side_effect = reflect_exc
    else:
        reflect.return_value = reflect_return
    return SimpleNamespace(
        pfc=SimpleNamespace(get_tool_use_total=lambda: 0),
        get_recollection=AsyncMock(return_value=[]),
        reflect=reflect,
        memory_store=None,
    )


class TestExecutorRecordsHistory:
    async def test_success_recorded_with_preview(self) -> None:
        mind = _fake_mind(reflect_return="任务产出内容")
        executor = TaskExecutor(mind)  # type: ignore[arg-type]
        task = TaskDefinition(name="ok_task", prompt="p", save_result_to_memory=False)

        result = await executor.run(task, trigger="heartbeat")
        assert result is not None

        records = task_history.get_history("ok_task")
        assert len(records) == 1
        rec = records[0]
        assert rec["status"] == "success"
        assert rec["trigger"] == "heartbeat"
        assert rec["preview"].startswith("任务产出内容")
        assert rec["duration_ms"] >= 0

    async def test_no_output_recorded(self) -> None:
        mind = _fake_mind(reflect_return="")
        executor = TaskExecutor(mind)  # type: ignore[arg-type]
        task = TaskDefinition(name="empty_task", prompt="p", save_result_to_memory=False)

        assert await executor.run(task, trigger="idle") is None
        rec = task_history.get_history("empty_task")[0]
        assert rec["status"] == "no_output"
        assert rec["trigger"] == "idle"

    async def test_null_keyword_recorded_with_preview(self) -> None:
        mind = _fake_mind(reflect_return="暂无述求")
        executor = TaskExecutor(mind)  # type: ignore[arg-type]
        task = TaskDefinition(
            name="kw_task", prompt="p", null_keywords=["暂无述求"],
            save_result_to_memory=False,
        )

        assert await executor.run(task) is None
        rec = task_history.get_history("kw_task")[0]
        assert rec["status"] == "no_output"
        assert rec["preview"] == "暂无述求"
        assert rec["trigger"] == "manual"  # 默认触发来源

    async def test_error_recorded_and_reraised(self) -> None:
        mind = _fake_mind(reflect_exc=RuntimeError("LLM 连接失败"))
        executor = TaskExecutor(mind)  # type: ignore[arg-type]
        task = TaskDefinition(name="bad_task", prompt="p", save_result_to_memory=False)

        with pytest.raises(RuntimeError, match="LLM 连接失败"):
            await executor.run(task, trigger="scheduled")

        rec = task_history.get_history("bad_task")[0]
        assert rec["status"] == "error"
        assert rec["trigger"] == "scheduled"
        assert "LLM 连接失败" in rec["error"]

    async def test_precheck_skip_not_recorded(self) -> None:
        """prompt 为空 / scope 不匹配属配置问题而非执行，不产生历史记录。"""
        executor = TaskExecutor(_fake_mind())  # type: ignore[arg-type]
        assert await executor.run(TaskDefinition(name="no_prompt", prompt="")) is None
        assert await executor.run(
            TaskDefinition(name="scope_mismatch", prompt="p", scope="entity"),
        ) is None
        assert task_history.get_history("no_prompt") == []
        assert task_history.get_history("scope_mismatch") == []
