"""任务执行历史单元测试：存储读写 / 上限裁剪 / 摘要 / 清理 / 执行器终态记录 / 调度事实源。"""

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
    monkeypatch.setattr("agent.mind.recall.coordinator.prepare_recollection", AsyncMock(return_value=[]))
    yield


def _record(name: str, started_at: float, *, status: str = "success", trigger: str = "manual") -> bool:
    return task_history.record_execution(
        name, started_at=started_at, duration_ms=1000,
        status=status, trigger=trigger, preview=f"产出 {started_at}",
    )


class TestHistoryStore:
    @pytest.mark.parametrize("trigger", ["manual", "event"])
    def test_record_and_get_newest_first(self, trigger: str) -> None:
        assert _record("t1", 100.0, trigger=trigger)
        assert _record("t1", 200.0, trigger=trigger)
        assert _record("t1", 300.0, trigger=trigger)

        records = task_history.get_history("t1")
        assert [r["started_at"] for r in records] == [300.0, 200.0, 100.0]
        first = records[0]
        assert first["task_name"] == "t1"
        assert first["duration_ms"] == 1000
        assert first["status"] == "success"
        assert first["trigger"] == trigger

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


class TestPruneOrphans:
    """对账清理：无对应任务文件的历史条目被清除（心跳引擎构造/reload 调用）。"""

    def test_prunes_stale_keeps_valid(self) -> None:
        _record("alive", 100.0)
        _record("gone", 200.0)

        pruned = task_history.prune_orphans({"alive"})

        assert pruned == ["gone"]
        assert task_history.get_history("gone") == []
        assert len(task_history.get_history("alive")) == 1

    def test_no_stale_returns_empty(self) -> None:
        _record("alive", 100.0)
        assert task_history.prune_orphans({"alive", "other"}) == []

    def test_empty_history_noop(self) -> None:
        assert task_history.prune_orphans({"alive"}) == []

    def test_empty_valid_set_wipes_all(self) -> None:
        """空存活集语义 = 全部清理（调用方负责在目录不可枚举时跳过，见引擎守卫）。"""
        _record("a", 1.0)
        _record("b", 2.0)
        assert task_history.prune_orphans(set()) == ["a", "b"]
        assert task_history.get_summary() == {}


class TestLastGoodRuns:
    """调度槽位去重的事实源：只认 success/no_output，返回最近一次非失败的时间戳。"""

    def test_good_statuses_counted_error_skipped(self) -> None:
        _record("a", 100.0, status="success")
        _record("b", 200.0, status="error")
        _record("c", 300.0, status="no_output")

        runs = task_history.get_last_settled_runs()
        assert runs["a"] == 100.0
        assert "b" not in runs  # 只有失败记录 = 未成功跑过（保留重试语义）
        assert runs["c"] == 300.0

    def test_later_error_does_not_hide_earlier_good(self) -> None:
        """成功后的失败重试不抹掉"已跑过"事实。"""
        _record("a", 100.0, status="success")
        _record("a", 200.0, status="error")

        assert task_history.get_last_settled_runs()["a"] == 100.0

    def test_empty_history(self) -> None:
        assert task_history.get_last_settled_runs() == {}


def _fake_mind(reflect_return: str = "", reflect_exc: Exception | None = None) -> SimpleNamespace:
    reflect = AsyncMock()
    if reflect_exc is not None:
        reflect.side_effect = reflect_exc
    else:
        async def complete(*args, **kwargs):
            if kwargs.get("completion") is not None:
                kwargs["completion"].update(reason="completed", messages=[])
            return reflect_return
        reflect.side_effect = complete
    return SimpleNamespace(
        reflect=reflect,
        memory_store=None,
    )


class TestExecutorRecordsHistory:
    async def test_success_recorded_with_preview(self) -> None:
        mind = _fake_mind(reflect_return="任务产出内容")
        executor = TaskExecutor(mind, mind)  # type: ignore[arg-type]
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
        executor = TaskExecutor(mind, mind)  # type: ignore[arg-type]
        task = TaskDefinition(name="empty_task", prompt="p", save_result_to_memory=False)

        assert await executor.run(task, trigger="idle") is None
        rec = task_history.get_history("empty_task")[0]
        assert rec["status"] == "no_output"
        assert rec["trigger"] == "idle"

    async def test_null_keyword_recorded_with_preview(self) -> None:
        mind = _fake_mind(reflect_return="暂无述求")
        executor = TaskExecutor(mind, mind)  # type: ignore[arg-type]
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
        executor = TaskExecutor(mind, mind)  # type: ignore[arg-type]
        task = TaskDefinition(name="bad_task", prompt="p", save_result_to_memory=False)

        with pytest.raises(RuntimeError, match="LLM 连接失败"):
            await executor.run(task, trigger="scheduled")

        rec = task_history.get_history("bad_task")[0]
        assert rec["status"] == "error"
        assert rec["trigger"] == "scheduled"
        assert "LLM 连接失败" in rec["error"]

    async def test_precheck_skip_not_recorded(self) -> None:
        """prompt 为空 / scope 不匹配属配置问题而非执行，不产生历史记录。"""
        mind = _fake_mind()
        executor = TaskExecutor(mind, mind)  # type: ignore[arg-type]
        assert await executor.run(TaskDefinition(name="no_prompt", prompt="")) is None
        assert await executor.run(
            TaskDefinition(name="scope_mismatch", prompt="p", scope="entity"),
        ) is None
        assert task_history.get_history("no_prompt") == []
        assert task_history.get_history("scope_mismatch") == []


@pytest.mark.parametrize("reason", ["budget_exhausted", "interrupted", "failed", "unknown"])
@pytest.mark.parametrize("text", ["", "已写文件，待验收"])
async def test_incomplete_execution_preserves_progress_without_certifying_or_memorizing(reason, text):
    mind = _fake_mind()
    async def run(*args, **kwargs):
        kwargs["completion"].update(reason=reason, messages=[{"role": "tool", "content": '{"ok":true}'}])
        return text
    mind.reflect.side_effect = run
    executor = TaskExecutor(mind, mind)
    executor._store_result = AsyncMock()
    result = await executor.run(TaskDefinition(name="partial", prompt="p"), trigger="scheduled")
    assert result is not None and not result.completion_ready
    assert "未确认完成" in result.content and "不要从头重试" in result.content
    assert text in result.content
    executor._store_result.assert_not_awaited()
    record = task_history.get_history("partial")[0]
    assert record["status"] == "incomplete"
    assert "partial" in task_history.get_last_settled_runs()


@pytest.mark.parametrize("receipt, expected", [('{"error":"failed"}', "incomplete"), ('{"outcome":"unknown","success":false}', "incomplete"), ('{"ok":true}', "no_output")])
async def test_no_summary_does_not_turn_tool_invocation_count_into_success(receipt, expected):
    mind = _fake_mind()
    async def run(*args, **kwargs):
        kwargs["completion"].update(reason="completed", messages=[{"role": "tool", "content": receipt}])
        return ""
    mind.reflect.side_effect = run
    result = await TaskExecutor(mind, mind).run(TaskDefinition(name="receipts", prompt="p", save_result_to_memory=False))
    assert result is not None
    assert "副作用已完成" not in result.content
    assert task_history.get_history("receipts")[0]["status"] == expected


async def test_cancellation_records_uncertainty_without_success_or_retry():
    import asyncio
    entered = asyncio.Event()
    mind = _fake_mind()
    async def run(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    mind.reflect.side_effect = run
    execution = asyncio.create_task(TaskExecutor(mind, mind).run(TaskDefinition(name="cancelled", prompt="p")))
    await entered.wait()
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution
    assert task_history.get_history("cancelled")[0]["status"] == "incomplete"


async def test_exception_after_tool_attempt_is_not_automatically_replayed_or_memorized():
    mind = _fake_mind()

    async def run(*args, **kwargs):
        kwargs["completion"].update(reason="failed", messages=[
            {"role": "assistant", "tool_calls": [{"id": "write", "function": {"name": "write_file"}}]},
        ])
        raise ConnectionError("model disconnected after write")

    mind.reflect.side_effect = run
    executor = TaskExecutor(mind, mind)
    executor._store_result = AsyncMock()
    result = await executor.run(TaskDefinition(name="uncertain", prompt="p"))
    assert not result.completion_ready and "先核验" in result.content
    assert task_history.get_history("uncertain")[0]["status"] == "incomplete"
    assert "uncertain" in task_history.get_last_settled_runs()
    executor._store_result.assert_not_awaited()
