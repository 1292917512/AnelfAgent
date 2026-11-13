"""工作流 journal 测试：终态一笔写 / 事件单调序 / 导入表 / 收敛与清理。"""

import time
from types import SimpleNamespace

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


async def test_latest_attempt_keeps_failure_and_unknown_effect(journal: WorkflowJournal):
    await journal.create_run("parent", "t", "{}", "h", "")
    await journal.admit_node("parent", "a", 1, "ask", "ih1", "{}")
    await journal.settle_node("parent", "a", 1, result_text="v1")
    await journal.admit_node("parent", "a", 2, "ask", "ih1", "{}")
    await journal.settle_node("parent", "a", 2, result_text="v2")
    await journal.admit_node("parent", "b", 1, "tool", "ihb", "{}")
    await journal.settle_node("parent", "b", 1, error_text="x")  # 失败步不可导入
    await journal.admit_node("parent", "a", 3, "ask", "ih1", "{}")
    await journal.mark_dispatched("parent", "a", 3, replay_safe=False)
    nodes = await journal.latest_nodes("parent")
    assert nodes["a"]["ordinal"] == 3
    assert nodes["a"]["dispatch_state"] == "dispatched"
    assert nodes["b"]["status"] == "failed"
    with pytest.raises(ValueError, match="在途节点"):
        await journal.mark_dispatched("parent", "b", 1, replay_safe=False)


async def test_legacy_node_migration_keeps_unknown_dispatch_and_result_completeness(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE workflow_node (
            run_id TEXT, step_key TEXT, ordinal INTEGER, kind TEXT, status TEXT,
            input_hash TEXT, input_json TEXT, result_text TEXT, error_text TEXT,
            delegation_id TEXT, usage_json TEXT, created_at REAL, updated_at REAL,
            PRIMARY KEY(run_id, step_key, ordinal))""")
        connection.executemany(
            "INSERT INTO workflow_node VALUES ('legacy', ?, 1, 'tool', ?, 'oldhash', '{}', ?, NULL, NULL, NULL, 0, 0)",
            [("complete", "completed", '{"verified":true}'),
             ("truncated", "completed", "x" * 16000 + "…[截断，原长度 20000]"),
             ("inflight", "running", None)],
        )
    for _ in range(2):
        journal = WorkflowJournal(str(path))
        try:
            nodes = await journal.latest_nodes("legacy")
            assert nodes["complete"]["result_complete"] == 1
            assert nodes["truncated"]["result_complete"] == 0
            assert nodes["inflight"]["dispatch_state"] == "unknown"
            assert nodes["inflight"]["replay_safe"] == 0
        finally:
            await journal.aclose()


@pytest.mark.parametrize("completed", [False, True])
async def test_durable_receipt_survives_abrupt_process_exit(tmp_path, completed):
    import asyncio
    import os
    import subprocess
    import sys
    import textwrap

    path = tmp_path / "crash.sqlite3"
    code = textwrap.dedent("""
        import asyncio, os, sys
        from agent.workflow.journal import WorkflowJournal

        async def main():
            journal = WorkflowJournal(sys.argv[1])
            await journal.create_run("crash", "crash", "{}", "spec", "")
            await journal.admit_node("crash", "effect", 1, "tool", "input")
            await journal.mark_dispatched("crash", "effect", 1, replay_safe=False)
            if sys.argv[2] == "True":
                await journal.settle_node("crash", "effect", 1, result_text='{"done":true}')
            os._exit(71)

        asyncio.run(main())
    """)
    env = {**os.environ, "ANELF_CONFIG_DIR": str(tmp_path / "config"), "ANELF_DATA_DIR": str(tmp_path / "data")}
    process = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", code, str(path), str(completed)],
                                      env=env, capture_output=True, timeout=20)
    assert process.returncode == 71, process.stderr.decode(errors="replace")
    journal = WorkflowJournal(str(path))
    try:
        node = await journal.get_node("crash", "effect", 1)
        assert node["status"] == ("completed" if completed else "running")
        assert node["dispatch_state"] == ("settled" if completed else "dispatched")
        assert node["replay_safe"] == 0
        if completed:
            assert node["result_complete"] == 1
            assert node["result_text"] == '{"done":true}'
    finally:
        await journal.aclose()


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


async def test_cleanup_preserves_running_resumable_sources_and_definitions(journal: WorkflowJournal, monkeypatch):
    monkeypatch.setattr(wf_journal, "time", SimpleNamespace(time=lambda: 1000.0))
    for run_id, status in [("source", "completed"), ("stopped", "stopped"), ("failed", "failed"), ("live", "running")]:
        await journal.create_run(run_id, run_id, "{}", "h", "", "source" if run_id == "stopped" else "")
        await journal.admit_node(run_id, "a", 1, "ask", "h")
        await journal.append_event(run_id, "run-started")
        if status != "running":
            await journal.settle_run(run_id, status)
    await journal.save_definition("definition", "saved", "{}", create=True)
    with pytest.raises(ValueError, match="引用"):
        await journal.delete_run("source")
    with pytest.raises(ValueError, match="运行中"):
        await journal.delete_run("live")
    with pytest.raises(ValueError):
        await journal.cleanup_runs(-1)
    assert await journal.cleanup_runs(0, protected_ids=["failed"]) == 0
    assert await journal.cleanup_runs(0) == 1
    assert await journal.nodes_of_run("failed") == []
    assert await journal.list_events("failed", -1) == []
    assert await journal.cleanup_runs(0, include_stopped=True) == 2
    assert await journal.cleanup_runs(0) == 0
    assert [run["id"] for run in await journal.list_runs()] == ["live"]
    assert (await journal.get_definition("definition"))["name"] == "saved"


async def test_run_delete_rolls_back_all_three_tables_on_failure(journal: WorkflowJournal):
    import sqlite3

    await journal.create_run("r", "r", "{}", "h", "")
    await journal.admit_node("r", "a", 1, "ask", "h")
    await journal.append_event("r", "run-started")
    await journal.settle_run("r", "completed")
    with sqlite3.connect(journal._db_path) as conn:
        conn.execute("CREATE TRIGGER reject_delete BEFORE DELETE ON workflow_event"
                     " BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        await journal.delete_run("r")
    assert await journal.get_run("r") is not None
    assert len(await journal.nodes_of_run("r")) == 1
    assert len(await journal.list_events("r", -1)) == 1
