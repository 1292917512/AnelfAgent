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


async def _approval_ok(self, step, run):
    from agent.approval.session import ApprovalDecision
    return ApprovalDecision.APPROVED


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
        await wait_run_terminal(engine.journal, (await engine.list_runs(1))[0]["id"])
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
    async def test_interrupted_runs_converged(self, engine, stub_mind):
        stub_mind.workflow_engine = engine  # recovery 经 mind.workflow_engine 收口
        # 直接造崩溃残留：running run + running 节点行（时间戳回拨到引擎启动前，
        # 模拟上一进程遗留）
        await engine.journal.create_run("zombie", "僵尸", "{}", "h", "user_x")
        await engine.journal.admit_node("zombie", "a", 1, "ask", "ih", "{}")
        _rewind_created_at(engine, "zombie")

        from agent.workflow.recovery import recover_interrupted_workflows
        recovered = await recover_interrupted_workflows(stub_mind)
        assert [r["id"] for r in recovered] == ["zombie"]

        run = await engine.journal.get_run("zombie")
        assert run["status"] == wf_journal.STOPPED
        assert run["stop_reason"] == "interrupted"

        # 二次收敛幂等（无 running 残留）
        assert await recover_interrupted_workflows(stub_mind) == []

    async def test_recovery_skips_live_and_fresh_runs(self, engine, stub_mind):
        """恢复守卫：在飞 run 与引擎启动后新建的 running run 不被误收敛。"""
        stub_mind.workflow_engine = engine
        engine._runs["live"] = type("R", (), {})()  # 在飞表占位（不真跑）
        await engine.journal.create_run("live", "在飞", "{}", "h", "")
        await engine.journal.create_run("fresh", "新建", "{}", "h", "")  # 启动后创建

        from agent.workflow.recovery import recover_interrupted_workflows
        assert await recover_interrupted_workflows(stub_mind) == []
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
