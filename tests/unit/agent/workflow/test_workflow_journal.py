"""工作流 journal 测试：终态一笔写 / 事件单调序 / 导入表 / 收敛与清理。"""

import time

import pytest

from agent.workflow import journal as wf_journal
from agent.workflow.journal import WorkflowJournal


@pytest.fixture()
def journal():
    j = WorkflowJournal()
    yield j
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if not loop.is_closed():
            loop.run_until_complete(j.aclose())
    except Exception:
        pass


async def test_run_lifecycle_one_write(journal: WorkflowJournal):
    await journal.create_run("r1", "测试", "{}", "hash1", "user_x")
    run = await journal.get_run("r1")
    assert run["status"] == wf_journal.RUNNING
    assert run["finished_at"] is None

    # 终态一笔写：status 与结算袋同笔落
    assert await journal.settle_run(
        "r1", wf_journal.COMPLETED, result={"steps": {}}) is True
    run = await journal.get_run("r1")
    assert run["status"] == wf_journal.COMPLETED
    assert run["result_json"] == '{"steps": {}}'
    assert run["finished_at"] is not None

    # 再次结算覆盖前值（重试语义），未知 run 返回 False
    assert await journal.settle_run("ghost", wf_journal.FAILED) is False


async def test_reopen_clears_settlement(journal: WorkflowJournal):
    await journal.create_run("r1", "测试", "{}", "hash1", "")
    await journal.settle_run("r1", wf_journal.STOPPED, stop_reason="user",
                             failure={"message": "停"})
    assert await journal.reopen_run("r1") is True
    run = await journal.get_run("r1")
    assert run["status"] == wf_journal.RUNNING
    assert run["stop_reason"] is None
    assert run["failure_json"] is None
    assert run["result_json"] is None
    assert run["finished_at"] is None


async def test_node_admit_settle_and_latest(journal: WorkflowJournal):
    await journal.create_run("r1", "t", "{}", "h", "")
    await journal.admit_node("r1", "step_a", 1, "ask", "ih1", "{}")
    node = await journal.get_node("r1", "step_a", 1)
    assert node["status"] == wf_journal.NODE_RUNNING
    assert node["input_hash"] == "ih1"

    await journal.settle_node("r1", "step_a", 1, result_text="done",
                              delegation_id="d1", usage={"turns": 3})
    node = await journal.get_node("r1", "step_a", 1)
    assert node["status"] == wf_journal.NODE_COMPLETED
    assert node["result_text"] == "done"
    assert node["usage_json"] == '{"turns": 3}'

    # 重试轮次占新 ordinal；latest_nodes 取最大
    await journal.admit_node("r1", "step_a", 2, "ask", "ih2", "{}")
    await journal.settle_node("r1", "step_a", 2, error_text="失败")
    latest = await journal.latest_nodes("r1")
    assert latest["step_a"]["ordinal"] == 2
    assert latest["step_a"]["status"] == wf_journal.NODE_FAILED

    assert await journal.next_ordinal("r1", "step_a") == 3
    assert await journal.next_ordinal("r1", "fresh") == 1


async def test_events_monotonic_sequence(journal: WorkflowJournal):
    await journal.create_run("r1", "t", "{}", "h", "")
    s1 = await journal.append_event("r1", "run-started", {"name": "t"})
    s2 = await journal.append_event("r1", "node-admitted", {"key": "a"})
    s3 = await journal.append_event("r1", "run-settled", {"status": "completed"})
    assert (s1, s2, s3) == (0, 1, 2)

    events = await journal.list_events("r1", after_sequence=0)
    assert [e["type"] for e in events] == ["node-admitted", "run-settled"]
    assert events[-1]["payload"] == {"status": "completed"}


async def test_importable_nodes_picks_latest_completed(journal: WorkflowJournal):
    await journal.create_run("parent", "t", "{}", "h", "")
    await journal.admit_node("parent", "a", 1, "ask", "ih1", "{}")
    await journal.settle_node("parent", "a", 1, result_text="v1")
    await journal.admit_node("parent", "a", 2, "ask", "ih1", "{}")
    await journal.settle_node("parent", "a", 2, result_text="v2")
    await journal.admit_node("parent", "b", 1, "tool", "ihb", "{}")
    await journal.settle_node("parent", "b", 1, error_text="x")  # 失败步不可导入

    imports = await journal.importable_nodes("parent")
    assert set(imports) == {"a"}
    assert imports["a"]["result_text"] == "v2"


async def test_list_non_terminal_and_purge(journal: WorkflowJournal):
    await journal.create_run("stale", "t", "{}", "h", "")
    await journal.create_run("done", "t", "{}", "h", "")
    await journal.settle_run("done", wf_journal.COMPLETED, result={})

    stale = await journal.list_non_terminal_runs()
    assert [r["id"] for r in stale] == ["stale"]
    await journal.settle_run("stale", wf_journal.STOPPED, stop_reason="interrupted")
    assert await journal.list_non_terminal_runs() == []

    # retention：终态且超期的 run 连带清理（造旧时间戳）
    import sqlite3
    conn = sqlite3.connect(journal._db_path)
    conn.execute("UPDATE workflow_run SET finished_at = ?", (time.time() - 86400 * 31,))
    conn.commit()
    conn.close()
    assert await journal.purge_expired(30) == 2
    assert await journal.get_run("done") is None
