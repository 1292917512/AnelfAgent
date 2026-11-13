"""工作流引擎测试：拓扑执行 / 上下文注入 / 门控重跑 / 断点续跑 / 修订导入 / 崩溃收敛。"""

import asyncio
import json

import pytest
from wf_helpers import (
    wait_run_terminal,
    wait_until,
)

from agent.workflow import journal as wf_journal
from agent.workflow.spec import WorkflowSpecError


@pytest.mark.parametrize("read_first,notice", [(False, ""), (True, ""), (True, "permission notice")])
async def test_skill_prerequisite_is_enforced_in_workflow(engine, tmp_path, monkeypatch, read_first, notice):
    from functools import partial
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agent.skills.execution import check_required_skills
    from agent.skills.skill_matcher import SkillMatcher
    from agent.skills.skill_store import SkillStore
    from agent.skills.tools import SkillToolDeps, get_skill, skill_tools_port
    from core.entity import EntityRegistry
    from core.tool_preconditions import tool_precondition_port

    store = SkillStore(str(tmp_path / "skills"))
    store.create("publishing", "发布检查", "先检查目标与素材，再执行发布")
    store.set_required_tools("publishing", ["test_workflow_publish"])
    calls = []

    async def publish() -> str:
        calls.append(True)
        return "ok"

    skill_tools_port.set(SkillToolDeps(store, SkillMatcher(store)))
    tool_precondition_port.set(partial(check_required_skills, store))
    EntityRegistry.register_tool(name="test_workflow_publish", func=publish, group="tests")
    EntityRegistry.register_tool(name="test_workflow_read", func=lambda: get_skill("publishing"), group="tests")
    monkeypatch.setattr(engine, "_request_approval", AsyncMock(return_value=SimpleNamespace(allowed=True, notice=notice)))
    steps = ([{"key": "read", "kind": "tool", "tool": "test_workflow_read", "args": {}}]
             if read_first else [])
    steps.append({"key": "publish", "kind": "tool", "tool": "test_workflow_publish", "args": {},
                  "depends_on": ["read"] if read_first else [], "retries": 2})
    try:
        summary = await engine.start({"name": "skill gate", "steps": steps})
        run = await wait_run_terminal(engine.journal, summary["run_id"])
        nodes = await engine.journal.nodes_of_run(summary["run_id"])
        assert run["status"] == (wf_journal.COMPLETED if read_first else wf_journal.FAILED), [n["error_text"] for n in nodes]
        assert len(calls) == int(read_first)
    finally:
        EntityRegistry.unregister("test_workflow_publish")
        EntityRegistry.unregister("test_workflow_read")
        skill_tools_port.unbind()
        tool_precondition_port.unbind()


async def test_saved_definition_is_durable_and_runs_use_independent_snapshots(engine, fake_manager):
    from agent.workflow.journal import WorkflowJournal

    spec = {"name": "saved", "steps": [{"key": "a", "goal": "original"}]}
    definition = await engine.save_definition(spec)
    workflow_id = definition["workflow_id"]
    assert not fake_manager.calls
    reopened = WorkflowJournal()
    try:
        assert (await reopened.get_definition(workflow_id))["spec"] == definition["spec"]
    finally:
        await reopened.aclose()
    first = await engine.start(workflow_id=workflow_id)
    await wait_until(lambda: not engine.is_running(first["run_id"]))
    edited = {"name": "edited", "steps": [{"key": "a", "goal": "updated"}]}
    await engine.save_definition(edited, workflow_id)
    second = await engine.start(workflow_id=workflow_id)
    await wait_until(lambda: not engine.is_running(second["run_id"]))
    assert first["run_id"] != second["run_id"]
    await engine.delete_definition(workflow_id)
    assert await engine.list_definitions() == []
    assert (await engine.run_detail(first["run_id"]))["spec"]["steps"][0]["goal"] == "original"
    assert (await engine.run_detail(second["run_id"]))["spec"]["steps"][0]["goal"] == "updated"
    assert [call["goal"] for call in fake_manager.calls] == ["original", "updated"]
    with pytest.raises(ValueError):
        await engine.save_definition(edited, workflow_id)
    with pytest.raises(ValueError):
        await engine.start(workflow_id=workflow_id)
    with pytest.raises(ValueError):
        await engine.start(spec, workflow_id="ambiguous")


async def test_cleanup_and_delete_protect_run_until_final_event_is_written(engine, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    append = engine.journal.append_event

    async def delayed_append(run_id, event_type, payload=None):
        if event_type == "run-settled":
            entered.set()
            await release.wait()
        return await append(run_id, event_type, payload)

    monkeypatch.setattr(engine.journal, "append_event", delayed_append)
    run = await engine.start({"name": "settling", "steps": [{"key": "a", "goal": "work"}]})
    await asyncio.wait_for(entered.wait(), 5)
    with pytest.raises(ValueError, match="结算"):
        await engine.delete_run(run["run_id"])
    assert (await engine.cleanup_runs(0))["deleted"] == 0
    release.set()
    await wait_until(lambda: not engine.is_running(run["run_id"]))
    assert (await engine.delete_run(run["run_id"]))["ok"]
    assert await engine.journal.nodes_of_run(run["run_id"]) == []
    assert await engine.journal.list_events(run["run_id"], -1) == []


async def test_retention_keeps_expired_revision_source_for_import(engine, fake_manager):
    import sqlite3
    import time

    spec = {"name": "revision", "steps": [{"key": "a", "goal": "paid work"}]}
    parent = await engine.start(spec)
    await wait_until(lambda: not engine.is_running(parent["run_id"]))
    with sqlite3.connect(engine.journal._db_path) as conn:
        conn.execute("UPDATE workflow_run SET finished_at = ? WHERE id = ?",
                     (time.time() - 86400 * 40, parent["run_id"]))
    child = await engine.start(spec, resume_of=parent["run_id"])
    await wait_until(lambda: not engine.is_running(child["run_id"]))
    assert len(fake_manager.calls) == 1
    assert await engine.journal.get_run(parent["run_id"]) is not None
    assert (await engine.cleanup_runs(30))["deleted"] == 1
    assert (await engine.run_detail(child["run_id"]))["nodes"][0]["result"] == "out[paid work]"


@pytest.mark.parametrize(("reason", "schema_ok"), [
    ("budget_exhausted", None), ("completed", False),
])
async def test_incomplete_ask_preserves_progress_without_retrying(
        engine, fake_manager, monkeypatch, reason, schema_ok):
    from unittest.mock import AsyncMock

    from agent.delegation.sub_agent import SubAgentResult

    delegate = AsyncMock(return_value=SubAgentResult(
        goal="write", success=True, output="文件已写入，待核验",
        completed_reason=reason, schema_ok=schema_ok,
    ))
    monkeypatch.setattr(fake_manager, "delegate", delegate)
    summary = await engine.start({"name": "收束", "steps": [
        {"key": "a", "kind": "ask", "goal": "write", "retries": 2},
        {"key": "b", "kind": "ask", "goal": "publish", "depends_on": ["a"]},
    ]})
    run = await wait_run_terminal(engine.journal, summary["run_id"])
    assert run["status"] == wf_journal.FAILED
    assert delegate.await_count == 1
    nodes = await engine.journal.nodes_of_run(summary["run_id"])
    assert "未自动重试" in nodes[0]["error_text"]
    assert nodes[0]["result_text"] == "文件已写入，待核验"
    assert nodes[0]["delegation_id"]


async def _approval_ok(self, step, run):
    from agent.approval.types import ApprovalResult
    return ApprovalResult(True, "rule_allow")


@pytest.mark.parametrize("notice", ["", "approval notice"])
@pytest.mark.parametrize("oversized", [False, True])
async def test_tool_receipt_uses_committed_result(engine, monkeypatch, notice, oversized):
    from unittest.mock import AsyncMock

    from agent.approval.types import ApprovalResult
    from core.entity import EntityRegistry
    from core.tool_results import PresentedToolResult

    delivered = []
    content = "1→alpha\n2→" + "x" * (20000 if oversized else 20)
    source = PresentedToolResult(content, delivered.append)
    monkeypatch.setattr(EntityRegistry, "execute_tool", AsyncMock(return_value=source))
    monkeypatch.setattr(engine, "_request_approval", AsyncMock(return_value=ApprovalResult(
        True, "rule_allow", notice=notice,
    )))
    summary = await engine.start({"name": "receipt", "steps": [
        {"key": "read", "kind": "tool", "tool": "read_file", "args": {}},
    ]})
    run = await wait_run_terminal(engine.journal, summary["run_id"])
    assert run["status"] == wf_journal.COMPLETED
    nodes = await engine.journal.nodes_of_run(summary["run_id"])
    stored = nodes[0]["result_text"]
    assert delivered == [content]
    assert (json.loads(stored)["result"] if notice else stored) == content
    assert nodes[0]["result_complete"]
    detail = await engine.run_detail(summary["run_id"])
    assert (len(detail["nodes"][0]["result"]) < len(stored)) == oversized
    source.acknowledge(content)
    assert delivered == [content]


async def test_sensitive_receipt_is_ephemeral_and_never_replayed(engine, fake_manager, monkeypatch, approve_all):
    from core.entity import EntityRegistry

    secret = "temporary-secret-for-workflow-test"
    calls = []

    async def credential() -> str:
        calls.append(True)
        return json.dumps({"credential": secret})

    EntityRegistry.register_tool(name="test_workflow_credential", func=credential, group="tests",
                                 meta={"sensitive_result": True})
    spec = {"name": "sensitive", "steps": [
        {"key": "credential", "kind": "tool", "tool": "test_workflow_credential"},
        {"key": "consume", "goal": "consume", "depends_on": ["credential"]},
    ]}
    try:
        first = await engine.start(spec)
        await wait_until(lambda: not engine.is_running(first["run_id"]))
        assert secret in fake_manager.calls[0]["context"]
        node = (await engine.journal.latest_nodes(first["run_id"]))["credential"]
        assert not node["result_complete"]
        assert secret not in str(await engine.journal.nodes_of_run(first["run_id"]))
        assert secret not in json.dumps(await engine.run_detail(first["run_id"]))
        assert secret not in str(await engine.journal.get_run(first["run_id"]))
        second = await engine.start(spec, resume_of=first["run_id"])
        run = await wait_run_terminal(engine.journal, second["run_id"])
        assert run["status"] == wf_journal.FAILED
        assert "脱敏" in run["failure_json"]
        assert len(calls) == 1
    finally:
        EntityRegistry.unregister("test_workflow_credential")


@pytest.mark.parametrize("payload", [
    {"success": False, "message": "target not reached", "retryable": False},
    {"ok": False, "detail": "result unknown", "outcome": "unknown"},
    {"error": "write timed out", "retryable": False},
])
async def test_tool_failure_contract_stops_unsafe_retry(engine, monkeypatch, approve_all, payload):
    execute = _fake_tool_results([payload])
    monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", execute)
    summary = await engine.start({"name": "工具结果", "steps": [
        {"key": "write", "kind": "tool", "tool": "write_file", "args": {}, "retries": 2},
    ]})
    run = await wait_run_terminal(engine.journal, summary["run_id"])
    assert run["status"] == wf_journal.FAILED
    assert len(execute.calls) == 1
    nodes = await engine.journal.nodes_of_run(summary["run_id"])
    assert json.loads(nodes[0]["result_text"]) == payload


async def test_close_drains_steps_and_refuses_restart(engine, fake_manager):
    fake_manager.gates["blocked"] = asyncio.Event()
    spec = {"name": "close", "steps": [{"key": "a", "kind": "ask", "goal": "blocked"}]}
    summary = await engine.start(spec)
    await wait_until(lambda: bool(fake_manager.calls))
    task = engine._runs[summary["run_id"]].task
    await engine.aclose()
    assert task.done()
    assert engine.running_ids() == []
    assert engine.journal._conn is None
    with pytest.raises(RuntimeError):
        await engine.start(spec)
    with pytest.raises(RuntimeError):
        await engine.journal.get_run(summary["run_id"])


@pytest.fixture()
def approve_all(monkeypatch):
    monkeypatch.setattr("agent.workflow.engine.WorkflowEngine._request_approval", _approval_ok)


def _rewind_created_at(engine, run_id: str) -> None:
    """把 run 的 created_at 回拨到引擎启动前（模拟上一进程遗留）。"""
    import sqlite3

    conn = sqlite3.connect(engine.journal._db_path)
    try:
        conn.execute("UPDATE workflow_run SET created_at = ? WHERE id = ?",
                     (engine._started_at - 1.0, run_id))
        conn.commit()
    finally:
        conn.close()


def _fake_tool_results(results: list):
    """按调用次序返回结果的假 execute_tool（class 调用面）。"""
    calls: list = []

    async def _execute(name, arguments="", *, timeout=60.0):
        calls.append({"tool": name, "args": json.loads(arguments or "{}")})
        idx = min(len(calls) - 1, len(results) - 1)
        return json.dumps(results[idx], ensure_ascii=False)

    _execute.calls = calls
    return _execute


class TestHappyPath:
    async def test_dag_execution_with_context_injection(self, engine, fake_manager):
        summary = await engine.start({
            "name": "调研",
            "steps": [
                {"key": "a", "kind": "ask", "goal": "调研 A"},
                {"key": "b", "kind": "ask", "goal": "调研 B"},
                {"key": "c", "kind": "ask", "goal": "汇总", "depends_on": ["a", "b"]},
            ],
        })
        run = await wait_run_terminal(engine.journal, summary["run_id"])
        assert run["status"] == wf_journal.COMPLETED

        goals = [c["goal"] for c in fake_manager.calls]
        assert goals.count("调研 A") == 1 and goals.count("调研 B") == 1
        # 依赖结果注入：c 的 context 含 a/b 的产出
        c_call = next(c for c in fake_manager.calls if c["goal"] == "汇总")
        assert "out[调研 A]" in c_call["context"] and "out[调研 B]" in c_call["context"]

        result = json.loads(run["result_json"])
        assert set(result["steps"]) == {"a", "b", "c"}
        assert "out[汇总]" in result["steps"]["c"]["result"]

        # 事件流以 run-settled 收尾
        events = await engine.journal.list_events(summary["run_id"])
        assert events[-1]["type"] == "run-settled"

    async def test_continue_from_carries_transcript(self, engine, fake_manager, monkeypatch):
        from agent.delegation import journal as delegation_journal

        # 假件不落 transcript；包装 delegate 按其 delegation_id 补写，
        # 引擎的 continue_from 才有续聊数据源
        original_delegate = fake_manager.delegate

        async def _delegate_with_transcript(goal, context="", **kwargs):
            result = await original_delegate(goal, context, **kwargs)
            delegation_id = kwargs.get("delegation_id") or fake_manager.ids[-1]
            delegation_journal.save_transcript({
                "delegation_id": delegation_id,
                "goal": goal,
                "messages": [{"role": "assistant", "content": f"history of {goal}"}],
                "success": True,
                "output": result.output,
            })
            return result

        monkeypatch.setattr(fake_manager, "delegate", _delegate_with_transcript)

        summary = await engine.start({
            "name": "续聊",
            "steps": [
                {"key": "a", "kind": "ask", "goal": "第一步"},
                {"key": "b", "kind": "ask", "goal": "接着做", "continue_from": "a",
                 "depends_on": ["a"]},
            ],
        })
        await wait_run_terminal(engine.journal, summary["run_id"])
        b_call = next(c for c in fake_manager.calls if c["goal"] == "接着做")
        assert b_call["base_messages"] is not None
        assert any("history of 第一步" in str(m) for m in b_call["base_messages"])
        assert any("工作流续聊" in str(m) for m in b_call["base_messages"])


class TestFailure:
    async def test_step_failure_fails_run_with_failed_steps(self, engine, fake_manager):
        fake_manager.failures["会失败"] = 5
        summary = await engine.start({
            "name": "失败",
            "steps": [
                {"key": "a", "kind": "ask", "goal": "会失败", "retries": 1},
                {"key": "b", "kind": "ask", "goal": "不会执行", "depends_on": ["a"]},
            ],
        })
        run = await wait_run_terminal(engine.journal, summary["run_id"])
        assert run["status"] == wf_journal.FAILED
        failure = json.loads(run["failure_json"])
        assert failure["failed_steps"][0]["key"] == "a"
        # 重试生效：失败步执行 2 次（首轮 + 1 次重试），下游不执行
        assert len([c for c in fake_manager.calls if c["goal"] == "会失败"]) == 2
        assert not any(c["goal"] == "不会执行" for c in fake_manager.calls)


class TestGate:
    @pytest.mark.parametrize("result,field,expected,passed", [
        ('{}', 'value', None, False),
        ('{"value":null}', 'value', None, True),
        ('{"value":0}', 'value', False, False),
        ('{"value":1}', 'value', True, False),
        ('{"value":false}', 'value', False, True),
        ('{"value":1.0}', 'value', 1, True),
        ('{"value":{"nested":[1]}}', 'value', {"nested": [True]}, False),
        ('{"value":{"nested":[1]}}', 'value', {"nested": [1]}, True),
        ('{"value":[null]}', 'value.0', None, True),
        ('{"value":[null]}', 'value.1', None, False),
        ('{"value":NaN}', 'value', None, False),
        ('{"value":1e999}', 'value', float('inf'), False),
        ('truncated {"value":', 'value', None, False),
    ])
    def test_gate_preserves_json_types_and_missing_fields(self, result, field, expected, passed):
        from agent.workflow.contracts import check_gate
        from agent.workflow.spec import GateSpec

        assert check_gate(result, GateSpec(field=field, equals=expected))[0] is passed

    async def test_large_receipt_survives_resume_and_revision(self, engine, fake_manager, monkeypatch, approve_all):
        payload = {"padding": "证据" * 12000, "verified": True}
        tool = _fake_tool_results([payload])
        monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", tool)
        fake_manager.gates["wait"] = asyncio.Event()
        spec = {"name": "full receipt", "steps": [
            {"key": "check", "kind": "tool", "tool": "check", "args": {},
             "gate": {"field": "verified", "equals": True, "max_rounds": 1}},
            {"key": "wait", "goal": "wait", "depends_on": ["check"]},
        ]}
        parent = await engine.start(spec)
        await wait_until(lambda: bool(fake_manager.calls))
        engine.stop(parent["run_id"])
        await wait_until(lambda: not engine.is_running(parent["run_id"]))
        nodes = await engine.journal.latest_nodes(parent["run_id"])
        assert json.loads(nodes["check"]["result_text"]) == payload
        fake_manager.gates["wait"].set()
        await engine.resume(parent["run_id"])
        await wait_until(lambda: not engine.is_running(parent["run_id"]))
        child = await engine.start(spec, resume_of=parent["run_id"])
        run = await wait_run_terminal(engine.journal, child["run_id"])
        assert run["status"] == wf_journal.COMPLETED
        assert len(tool.calls) == 1
        assert len(fake_manager.calls[0]["context"]) < 2200

    async def test_gate_repair_loop(self, engine, fake_manager, monkeypatch, approve_all):
        tool = _fake_tool_results([
            {"exit_code": 1, "stderr": "boom"},   # 第 1 轮不过门
            {"exit_code": 0, "stdout": "ok"},     # 修复后过门
        ])
        monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", tool)

        summary = await engine.start({
            "name": "把关",
            "steps": [{
                "key": "check", "kind": "tool", "tool": "shell",
                "args": {"command": "make test"},
                "gate": {"field": "exit_code", "equals": 0, "max_rounds": 3},
            }],
        })
        run = await wait_run_terminal(engine.journal, summary["run_id"])
        assert run["status"] == wf_journal.COMPLETED
        # 工具跑了 2 轮，中间插入修复委托
        assert len(tool.calls) == 2
        assert tool.calls[0]["args"] == {"command": "make test"}
        assert any("未通过门控" in c["goal"] for c in fake_manager.calls)

        # 修复轮独立成行（kind=repair），主步骤两档 ordinal
        nodes = await engine.journal.nodes_of_run(summary["run_id"])
        kinds = {(n["step_key"], n["kind"]) for n in nodes}
        assert ("check#repair", "repair") in kinds
        check_ordinals = [n["ordinal"] for n in nodes if n["step_key"] == "check"]
        assert check_ordinals == [1, 2]

    async def test_gate_exhausted_fails(self, engine, fake_manager, monkeypatch, approve_all):
        tool = _fake_tool_results([{"exit_code": 1}])
        monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", tool)

        summary = await engine.start({
            "name": "把关失败",
            "steps": [{
                "key": "check", "kind": "tool", "tool": "shell", "args": {},
                "gate": {"field": "exit_code", "equals": 0, "max_rounds": 2},
            }],
        })
        run = await wait_run_terminal(engine.journal, summary["run_id"])
        assert run["status"] == wf_journal.FAILED
        assert "门控未通过" in run["failure_json"]

    async def test_tool_error_result_fails_step(self, engine, fake_manager,
                                                monkeypatch, approve_all):
        async def _execute(name, arguments="", *, timeout=60.0):
            return json.dumps({"error": "工具炸了"}, ensure_ascii=False)

        monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", _execute)
        summary = await engine.start({
            "name": "工具错",
            "steps": [{"key": "t", "kind": "tool", "tool": "shell", "args": {}}],
        })
        run = await wait_run_terminal(engine.journal, summary["run_id"])
        assert run["status"] == wf_journal.FAILED
        assert "工具炸了" in run["failure_json"]


class TestStopResume:
    async def test_stop_preserves_completed_resume_reuses(self, engine, fake_manager):
        # b 步阻塞：a 完成后停 → 断点续跑时 a 不重付费、b 重派
        gate = asyncio.Event()
        fake_manager.gates["慢步骤"] = gate

        summary = await engine.start({
            "name": "停止续跑",
            "steps": [
                {"key": "a", "kind": "ask", "goal": "快步骤"},
                {"key": "b", "kind": "ask", "goal": "慢步骤", "depends_on": ["a"]},
            ],
        })
        await wait_until(lambda: len(
            [c for c in fake_manager.calls if c["goal"] == "快步骤"]) == 1)
        # 等待 b 进入在飞（run 停在 b 阻塞上，不会终态）
        await wait_until(lambda: any(c["goal"] == "慢步骤" for c in fake_manager.calls))

        stop_result = engine.stop(summary["run_id"])
        assert stop_result["ok"] is True
        run = await wait_run_terminal(engine.journal, summary["run_id"])
        assert run["status"] == wf_journal.STOPPED
        assert run["stop_reason"] == "user"

        gate.set()  # 解除阻塞（取消路径不再消费结果）
        # 续跑：a 缓存结算，b 重派执行
        resumed = await engine.resume(summary["run_id"])
        run = await wait_run_terminal(engine.journal, resumed["run_id"])
        assert run["status"] == wf_journal.COMPLETED

        assert len([c for c in fake_manager.calls if c["goal"] == "快步骤"]) == 1
        assert len([c for c in fake_manager.calls if c["goal"] == "慢步骤"]) == 2

        # a 的缓存结算在事件流留痕
        events = await engine.journal.list_events(summary["run_id"])
        cached = [e for e in events if e["type"] == "node-settled" and e["payload"].get("cached")]
        assert any(e["payload"]["key"] == "a" for e in cached)

    async def test_resume_rejects_non_stopped(self, engine):
        summary = await engine.start({
            "name": "x", "steps": [{"key": "a", "kind": "ask", "goal": "g"}],
        })
        await wait_run_terminal(engine.journal, summary["run_id"])
        with pytest.raises(ValueError):
            await engine.resume(summary["run_id"])


class TestAmendImport:
    @pytest.mark.parametrize("changed,historical_revision", [(False, False), (True, False), (False, True)])
    async def test_legacy_revision_reconstructs_dependencies_from_source_receipts(
            self, engine, monkeypatch, approve_all, changed, historical_revision):
        import hashlib
        import sqlite3

        tool = _fake_tool_results([{"verified": True}])
        monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", tool)
        spec = {"name": "legacy", "steps": [
            {"key": "edit", "kind": "tool", "tool": "edit", "args": {"version": 1}},
            {"key": "verify", "kind": "tool", "tool": "verify", "depends_on": ["edit"]},
        ]}
        parent = await engine.start(spec)
        await wait_until(lambda: not engine.is_running(parent["run_id"]))
        with sqlite3.connect(engine.journal._db_path) as connection:
            if historical_revision:
                connection.execute("UPDATE workflow_run SET parent_run_id = 'expired-source' WHERE id = ?",
                                   (parent["run_id"],))
            for step in spec["steps"]:
                old_input = json.dumps({"tool": step["tool"], "args": step.get("args", {})},
                                       ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                connection.execute("UPDATE workflow_node SET input_hash = ?, result_origin = NULL WHERE run_id = ? AND step_key = ?",
                                   (hashlib.sha256(old_input.encode()).hexdigest()[:12], parent["run_id"], step["key"]))
        if changed:
            spec["steps"][0]["args"]["version"] = 2
        child = await engine.start(spec, resume_of=parent["run_id"])
        await wait_until(lambda: not engine.is_running(child["run_id"]))
        child_run = await engine.journal.get_run(child["run_id"])
        if historical_revision:
            assert child_run["status"] == wf_journal.FAILED
            assert "依赖来源" in child_run["failure_json"]
            assert len(tool.calls) == 2
            return
        assert child_run["status"] == wf_journal.COMPLETED
        assert len(tool.calls) == (4 if changed else 2)
        grandchild = await engine.start(spec, resume_of=child["run_id"])
        await wait_until(lambda: not engine.is_running(grandchild["run_id"]))
        assert (await engine.journal.get_run(grandchild["run_id"]))["status"] == wf_journal.COMPLETED
        assert len(tool.calls) == (4 if changed else 2)

    async def test_tool_dependency_changes_invalidate_descendants_only(self, engine, monkeypatch, approve_all):
        tool = _fake_tool_results([{"verified": True}])
        monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", tool)
        spec = {"name": "dependency evidence", "steps": [
            {"key": "edit", "kind": "tool", "tool": "edit", "args": {"version": 1}},
            {"key": "verify", "kind": "tool", "tool": "verify", "depends_on": ["edit"]},
            {"key": "report", "kind": "tool", "tool": "report", "depends_on": ["verify"]},
            {"key": "independent", "kind": "tool", "tool": "independent"},
        ]}
        parent = await engine.start(spec)
        await wait_until(lambda: not engine.is_running(parent["run_id"]))
        spec["steps"][0]["args"]["version"] = 2
        child = await engine.start(spec, resume_of=parent["run_id"])
        run = await wait_run_terminal(engine.journal, child["run_id"])
        assert run["status"] == wf_journal.COMPLETED
        names = [call["tool"] for call in tool.calls]
        assert {name: names.count(name) for name in names} == {
            "edit": 2, "verify": 2, "report": 2, "independent": 1}

    @pytest.mark.parametrize("continue_from", [False, True])
    async def test_equal_preview_does_not_hide_changed_dependency(self, engine, fake_manager, monkeypatch, continue_from):
        from agent.delegation.sub_agent import SubAgentResult

        delegate = fake_manager.delegate

        async def answer(goal, *args, **kwargs):
            result = await delegate(goal, *args, **kwargs)
            return SubAgentResult(goal=goal, output="same prefix" * 400 + goal, success=True) if goal != "inspect" else result

        monkeypatch.setattr(fake_manager, "delegate", answer)
        dependency = {"continue_from": "source"} if continue_from else {"depends_on": ["source"]}
        spec = {"name": "full dependency", "steps": [
            {"key": "source", "goal": "old"}, {"key": "inspect", "goal": "inspect", **dependency},
        ]}
        parent = await engine.start(spec)
        await wait_until(lambda: not engine.is_running(parent["run_id"]))
        spec["steps"][0]["goal"] = "new"
        child = await engine.start(spec, resume_of=parent["run_id"])
        run = await wait_run_terminal(engine.journal, child["run_id"])
        assert run["status"] == wf_journal.COMPLETED
        inspections = [call for call in fake_manager.calls if call["goal"] == "inspect"]
        assert len(inspections) == 2
        assert inspections[0]["context"] == inspections[1]["context"]

    async def test_revision_does_not_resurrect_success_before_failure(self, engine, monkeypatch, approve_all):
        tool = _fake_tool_results([{"verified": True}])
        monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", tool)
        spec = {"name": "latest attempt", "steps": [
            {"key": "check", "kind": "tool", "tool": "check"},
            {"key": "verify", "kind": "tool", "tool": "verify", "depends_on": ["check"]},
        ]}
        parent = await engine.start(spec)
        await wait_until(lambda: not engine.is_running(parent["run_id"]))
        node = (await engine.journal.latest_nodes(parent["run_id"]))["check"]
        await engine.journal.admit_node(parent["run_id"], "check", 2, "tool", node["input_hash"])
        await engine.journal.settle_node(parent["run_id"], "check", 2, error_text="later failure")
        child = await engine.start(spec, resume_of=parent["run_id"])
        run = await wait_run_terminal(engine.journal, child["run_id"])
        assert run["status"] == wf_journal.COMPLETED
        assert [call["tool"] for call in tool.calls] == ["check", "verify", "check", "verify"]

    async def test_revision_imports_matching_steps(self, engine, fake_manager):
        first = await engine.start({
            "name": "v1",
            "steps": [
                {"key": "a", "kind": "ask", "goal": "调研 A"},
                {"key": "b", "kind": "ask", "goal": "调研 B", "depends_on": ["a"]},
            ],
        })
        await wait_run_terminal(engine.journal, first["run_id"])

        second = await engine.start({
            "name": "v2",
            "steps": [
                {"key": "a", "kind": "ask", "goal": "调研 A"},          # 输入一致 → 导入
                {"key": "b", "kind": "ask", "goal": "换个角度调研 B", "depends_on": ["a"]},
            ],
        }, resume_of=first["run_id"])
        run = await wait_run_terminal(engine.journal, second["run_id"])
        assert run["status"] == wf_journal.COMPLETED

        goals = [c["goal"] for c in fake_manager.calls]
        assert goals.count("调研 A") == 1            # 未重跑
        assert goals.count("换个角度调研 B") == 1     # 新目标重跑
        # b 的 context 里拿到导入的 a 结果
        b_call = next(c for c in fake_manager.calls if c["goal"] == "换个角度调研 B")
        assert "out[调研 A]" in b_call["context"]

    async def test_divergent_input_cascades(self, engine, fake_manager):
        first = await engine.start({
            "name": "v1",
            "steps": [
                {"key": "a", "kind": "ask", "goal": "旧目标"},
            ],
        })
        await wait_run_terminal(engine.journal, first["run_id"])

        await engine.start({
            "name": "v2",
            "steps": [
                {"key": "a", "kind": "ask", "goal": "新目标"},  # 输入分歧 → 重跑
            ],
        }, resume_of=first["run_id"])
        await wait_run_terminal(engine.journal, (await engine.list_runs(1))[0]["run_id"])
        goals = [c["goal"] for c in fake_manager.calls]
        assert goals == ["旧目标", "新目标"]

    async def test_amend_rejects_running_parent(self, engine, fake_manager):
        gate = asyncio.Event()
        fake_manager.gates["阻塞"] = gate
        parent = await engine.start({
            "name": "p", "steps": [{"key": "a", "kind": "ask", "goal": "阻塞"}],
        })
        with pytest.raises(ValueError):
            await engine.start({
                "name": "c", "steps": [{"key": "a", "kind": "ask", "goal": "x"}],
            }, resume_of=parent["run_id"])
        engine.stop(parent["run_id"])
        gate.set()
        await wait_run_terminal(engine.journal, parent["run_id"])


class TestSpecGuard:
    async def test_invalid_spec_raises(self, engine):
        with pytest.raises(WorkflowSpecError):
            await engine.start({"name": "x", "steps": [{"key": "a", "kind": "ask"}]})


class TestRecovery:
    @pytest.mark.parametrize("revision", [False, True])
    async def test_legacy_resume_requires_proven_receipt_origin(self, engine, monkeypatch, revision):
        import hashlib
        from unittest.mock import AsyncMock

        from agent.workflow.spec import canonical_spec, parse_spec, spec_hash

        spec = parse_spec({"name": "legacy resume", "steps": [{"key": "effect", "kind": "tool", "tool": "effect"}]})
        await engine.journal.create_run("source", spec.name, canonical_spec(spec), spec_hash(spec), "")
        await engine.journal.settle_run("source", wf_journal.COMPLETED)
        await engine.journal.create_run("legacy", spec.name, canonical_spec(spec), spec_hash(spec), "",
                                        "source" if revision else "")
        old_hash = hashlib.sha256(b'{"args":{},"tool":"effect"}').hexdigest()[:12]
        await engine.journal.admit_node("legacy", "effect", 1, "tool", old_hash)
        await engine.journal.settle_node("legacy", "effect", 1, result_text='{"done":true}')
        await engine.journal.settle_run("legacy", wf_journal.STOPPED)
        execute = AsyncMock()
        monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", execute)
        await engine.resume("legacy")
        run = await wait_run_terminal(engine.journal, "legacy")
        assert run["status"] == (wf_journal.FAILED if revision else wf_journal.COMPLETED)
        execute.assert_not_awaited()

    @pytest.mark.parametrize("replay_safe", [False, True])
    @pytest.mark.parametrize("boundary", ["approval", "before_result", "after_result"])
    async def test_tool_crash_boundary_and_explicit_replay_policy(
            self, engine, fake_manager, stub_mind, monkeypatch, approve_all, boundary, replay_safe):
        from agent.workflow.engine import WorkflowEngine
        from agent.workflow.recovery import recover_interrupted_workflows
        from core.entity import EntityRegistry

        entered = asyncio.Event()
        calls = []

        async def effect() -> str:
            calls.append(True)
            return '{"done":true}'

        EntityRegistry.register_tool(name="test_workflow_effect", func=effect, group="tests",
                                     meta={"concurrency_safe": True, "replay_safe": replay_safe})
        settle = engine.journal.settle_node

        async def pause_settle(*args, **kwargs):
            if boundary == "after_result":
                await settle(*args, **kwargs)
            entered.set()
            await asyncio.Event().wait()

        async def pause_approval(*args):
            entered.set()
            await asyncio.Event().wait()

        if boundary == "approval":
            monkeypatch.setattr(engine, "_request_approval", pause_approval)
        else:
            monkeypatch.setattr(engine.journal, "settle_node", pause_settle)
        spec = {"name": "crash", "steps": [{"key": "effect", "kind": "tool", "tool": "test_workflow_effect"}]}
        restored = None
        try:
            first = await engine.start(spec)
            await asyncio.wait_for(entered.wait(), 5)
            await engine.aclose()
            restored = WorkflowEngine(stub_mind.background_tasks, fake_manager)
            await recover_interrupted_workflows(stub_mind, restored)
            await restored.resume(first["run_id"])
            await wait_until(lambda: not restored.is_running(first["run_id"]))
            run = await restored.journal.get_run(first["run_id"])
            unknown = boundary == "before_result" and not replay_safe
            assert run["status"] == (wf_journal.FAILED if unknown else wf_journal.COMPLETED)
            assert len(calls) == (2 if boundary == "before_result" and replay_safe else 1)
            if unknown:
                assert "结果未知" in run["failure_json"]
                for _ in range(2):
                    revised = await restored.start(spec, resume_of=run["id"])
                    await wait_until(lambda: not restored.is_running(revised["run_id"]))
                    run = await restored.journal.get_run(revised["run_id"])
                    assert run["status"] == wf_journal.FAILED
                assert len(calls) == 1
        finally:
            if restored is not None:
                await restored.aclose()
            EntityRegistry.unregister("test_workflow_effect")

    async def test_interrupted_runs_converged(self, engine, stub_mind):
        # 直接造崩溃残留：running run + running 节点行（时间戳回拨到引擎启动前，
        # 模拟上一进程遗留）
        await engine.journal.create_run("zombie", "僵尸", "{}", "h", "user_x")
        await engine.journal.admit_node("zombie", "a", 1, "ask", "ih", "{}")
        _rewind_created_at(engine, "zombie")

        from agent.workflow.recovery import recover_interrupted_workflows
        recovered = await recover_interrupted_workflows(stub_mind, engine)
        assert [r["id"] for r in recovered] == ["zombie"]

        run = await engine.journal.get_run("zombie")
        assert run["status"] == wf_journal.STOPPED
        assert run["stop_reason"] == "interrupted"

        # 二次收敛幂等（无 running 残留）
        assert await recover_interrupted_workflows(stub_mind, engine) == []

    async def test_recovery_skips_live_and_fresh_runs(self, engine, stub_mind):
        """恢复守卫：在飞 run 与引擎启动后新建的 running run 不被误收敛。"""
        engine._runs["live"] = type("R", (), {})()  # 在飞表占位（不真跑）
        await engine.journal.create_run("live", "在飞", "{}", "h", "")
        await engine.journal.create_run("fresh", "新建", "{}", "h", "")  # 启动后创建

        from agent.workflow.recovery import recover_interrupted_workflows
        assert await recover_interrupted_workflows(stub_mind, engine) == []
        assert (await engine.journal.get_run("live"))["status"] == wf_journal.RUNNING
        assert (await engine.journal.get_run("fresh"))["status"] == wf_journal.RUNNING
        engine._runs.pop("live", None)

    async def test_concurrent_resume_single_dispatch(self, engine, fake_manager):
        """并发续跑同一 run：开账-派发持锁，第二个调用被拒。"""
        gate = asyncio.Event()
        fake_manager.gates["慢"] = gate
        summary = await engine.start({
            "name": "并发续跑", "steps": [{"key": "a", "kind": "ask", "goal": "慢"}],
        })
        await wait_until(lambda: any(c["goal"] == "慢" for c in fake_manager.calls))
        engine.stop(summary["run_id"])
        await wait_run_terminal(engine.journal, summary["run_id"])
        gate.set()

        results = await asyncio.gather(
            engine.resume(summary["run_id"]),
            engine.resume(summary["run_id"]),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, ValueError)]
        assert len(errors) == 1 and "已在运行中" in str(errors[0])


async def test_explicit_context_changes_revision_input_and_reaches_continuation(engine, fake_manager):
    from agent.delegation import journal
    spec = {"name": "context", "steps": [{"key": "a", "goal": "compare", "context": "first evidence"}]}
    parent = await engine.start(spec)
    await wait_until(lambda: not engine.is_running(parent["run_id"]))
    assert fake_manager.calls[-1]["context"] == "first evidence"
    spec["steps"][0]["context"] = "revised evidence"
    child = await engine.start(spec, resume_of=parent["run_id"])
    await wait_until(lambda: not engine.is_running(child["run_id"]))
    assert len(fake_manager.calls) == 2
    assert fake_manager.calls[-1]["context"] == "revised evidence"
    source = "context-transcript"
    journal.save_transcript({"delegation_id": source, "messages": [{"role": "user", "content": "old evidence"}]})
    from agent.workflow.engine import _RunState
    messages = engine._continuation_messages(_RunState("id", "user_test:scope#one", "test"),
        {"a": {"delegation_id": source}}, "a", "continue", "current evidence")
    assert "current evidence" in messages[-1]["content"]


async def test_framework_failure_reaps_parallel_siblings_before_publishing_terminal(engine, fake_manager, monkeypatch):
    entered, cleanup = asyncio.Event(), asyncio.Event()
    original = engine._run_step
    async def fail(run_id, run, step, *args):
        if step.key == "broken":
            await entered.wait()
            raise OSError("journal write unavailable")
        return await original(run_id, run, step, *args)
    async def block(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
    monkeypatch.setattr(engine, "_run_step", fail)
    monkeypatch.setattr(fake_manager, "delegate", block)
    run = await engine.start({"name": "failure", "steps": [
        {"key": "broken", "goal": "fail"}, {"key": "sibling", "goal": "write"}]})
    await wait_until(lambda: not engine.is_running(run["run_id"]))
    assert cleanup.is_set()
    assert (await engine.journal.get_run(run["run_id"]))["status"] == wf_journal.FAILED
    assert not [task for task in asyncio.all_tasks() if task.get_name().startswith("workflow."+run["run_id"])]


@pytest.mark.parametrize("safe", [False, True])
async def test_tool_layer_respects_safety_and_engine_capacity(engine, monkeypatch, approve_all, safe):
    from core.config import ConfigManager
    from core.entity import EntityRegistry
    ConfigManager.set("workflow_max_parallel_steps", 2)
    active = peak = 0
    release = asyncio.Event()
    entered = asyncio.Event()
    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        try:
            await release.wait()
            return '{"ok":true}'
        finally:
            active -= 1
    name = "workflow_capacity_probe"
    EntityRegistry.register_tool(name=name, func=work, group="test", meta={"concurrency_safe": safe})
    try:
        run = await engine.start({"name": "capacity", "steps": [
            {"key": f"a{i}", "kind": "tool", "tool": name} for i in range(6)]})
        await asyncio.wait_for(entered.wait(), 2)
        # Read the owned tasks after every admitted dispatch has had a scheduling opportunity.
        for _ in range(6):
            await asyncio.sleep(0)
        assert peak <= (2 if safe else 1)
        release.set()
        await wait_until(lambda: not engine.is_running(run["run_id"]))
        assert (await engine.journal.get_run(run["run_id"]))["status"] == wf_journal.COMPLETED
    finally:
        release.set()
        EntityRegistry.unregister(name)


async def test_queued_step_timeout_never_enters_tool_body(engine, monkeypatch, approve_all):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    invoke = AsyncMock(return_value='{"ok":true}')
    monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", invoke)
    engine._dispatch_sem = asyncio.Semaphore(0)
    result = await asyncio.wait_for(engine._dispatch_tool({"name": "queued"},
        SimpleNamespace(tool="no_side_effect", args={}), timeout=.02), 2)
    assert result.status == "failed" and "尚未执行" in result.error
    invoke.assert_not_awaited()


async def test_failed_parallel_group_does_not_start_later_unsafe_tool(engine, fake_manager, monkeypatch, approve_all):
    from unittest.mock import AsyncMock

    fake_manager.failures["fail"] = 1
    invoke = AsyncMock(return_value='{"ok":true}')
    monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", invoke)
    run = await engine.start({"name": "barrier", "steps": [
        {"key": "first", "goal": "fail"}, {"key": "write", "kind": "tool", "tool": "write_file"},
    ]})
    await wait_until(lambda: not engine.is_running(run["run_id"]))
    assert (await engine.journal.get_run(run["run_id"]))["status"] == wf_journal.FAILED
    invoke.assert_not_awaited()
    assert [node["step_key"] for node in await engine.journal.nodes_of_run(run["run_id"])] == ["first"]


async def test_cancel_queued_ask_and_approval_timeout_do_not_admit_business_work(engine, fake_manager, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agent.workflow.engine import _RunState

    engine._dispatch_sem = asyncio.Semaphore(0)
    state = _RunState("queue", "user_webui:same#window", "queued")
    queued = asyncio.create_task(engine._dispatch_ask("wait", "", "", state, "a"))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert fake_manager.calls == []
    assert not engine._background_tasks.running(state.scope)
    engine._dispatch_sem = asyncio.Semaphore(1)

    async def approve(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(engine, "_request_approval", approve)
    invoke = AsyncMock()
    monkeypatch.setattr("core.entity.EntityRegistry.execute_tool", invoke)
    result = await engine._dispatch_tool({"name": "approval"}, SimpleNamespace(tool="write", args={}), timeout=.02)
    assert result.retryable and "尚未执行" in result.error
    invoke.assert_not_awaited()
    assert not engine._dispatch_sem.locked()


async def test_stop_one_channel_while_other_channel_runs_and_resume_keeps_completed_results(engine, fake_manager):
    fake_manager.gates["blocked"] = asyncio.Event()
    first = await engine.start({"name": "stop", "steps": [
        {"key": "a", "goal": "paid work"},
        {"key": "b", "goal": "blocked", "depends_on": ["a"]},
    ]}, scope="user_webui:same#one")
    await wait_until(lambda: any(call["goal"] == "blocked" for call in fake_manager.calls))
    second = await engine.start({"name": "independent", "steps": [{"key": "a", "goal": "other work"}]},
                                scope="user_telegram:same")
    assert (await asyncio.to_thread(engine.stop, first["run_id"]))["ok"]
    await wait_until(lambda: not engine.is_running(first["run_id"]) and not engine.is_running(second["run_id"]))
    assert (await engine.journal.get_run(first["run_id"]))["status"] == wf_journal.STOPPED
    assert (await engine.journal.get_run(second["run_id"]))["status"] == wf_journal.COMPLETED
    fake_manager.gates["blocked"].set()
    await engine.resume(first["run_id"])
    await wait_until(lambda: not engine.is_running(first["run_id"]))
    assert (await engine.journal.get_run(first["run_id"]))["status"] == wf_journal.COMPLETED
    assert sum(call["goal"] == "paid work" for call in fake_manager.calls) == 1
