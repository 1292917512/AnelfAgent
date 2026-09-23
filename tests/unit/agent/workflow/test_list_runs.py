"""list_runs 对外契约回归测试（run_id 映射 + running 标记）。"""

import pytest
from wf_helpers import FakeDelegationManager, make_stub_mind

from agent.workflow import journal as wf_journal
from agent.workflow.engine import WorkflowEngine


@pytest.fixture()
def engine():
    eng = WorkflowEngine(make_stub_mind(FakeDelegationManager()))
    yield eng
    import asyncio
    asyncio.get_event_loop().run_until_complete(eng.aclose())


async def test_list_runs_exposes_run_id(engine: WorkflowEngine):
    await engine.journal.create_run("abc123", "测试", "{}", "h", "")
    await engine.journal.settle_run("abc123", wf_journal.COMPLETED, result={})
    runs = await engine.list_runs(10)
    assert len(runs) == 1
    item = runs[0]
    # 对外契约：run_id 存在且 journal 内部 id 键不外泄（前端按 run_id 取值）
    assert item["run_id"] == "abc123"
    assert "id" not in item
    assert item["running"] is False
    assert item["status"] == wf_journal.COMPLETED


async def test_list_runs_marks_running(engine: WorkflowEngine):
    await engine.journal.create_run("live1", "在飞", "{}", "h", "")
    engine._runs["live1"] = object()  # 占位（不真跑）
    runs = await engine.list_runs(10)
    item = next(r for r in runs if r["run_id"] == "live1")
    assert item["running"] is True
    engine._runs.pop("live1", None)
