import json

import pytest

from agent.workflow.engine import WorkflowEngine
from services.workflow import WorkflowService, WorkflowServiceError


async def test_detail_includes_complete_outputs_declared_steps_and_latest_events(engine: WorkflowEngine) -> None:
    spec = {"name": "example", "steps": [
        {"key": "first", "kind": "ask", "goal": "research"},
        {"key": "next", "kind": "ask", "goal": "summarize", "depends_on": ["first"]},
    ]}
    await engine.journal.create_run("detail-test", "example", json.dumps(spec), "hash", "")
    await engine.journal.admit_node("detail-test", "first", 1, "ask", "hash")
    result = "complete output " * 100
    await engine.journal.settle_node("detail-test", "first", 1, result_text=result, usage={"input_tokens": 12})
    for index in range(402):
        await engine.journal.append_event("detail-test", "progress", {"index": index})
    detail = await engine.run_detail("detail-test")
    assert detail["spec"] == spec
    assert detail["nodes"][0]["result"] == result
    assert detail["nodes"][0]["usage"] == {"input_tokens": 12}
    assert detail["events"][-1]["payload"]["index"] == 401
    assert detail["events_truncated"]
    assert not detail["run"]["running"]


def test_validation_works_without_live_runtime() -> None:
    service = WorkflowService()
    result = service.validate({"name": "example", "steps": [
        {"key": "a", "goal": "research"}, {"key": "b", "goal": "summary", "depends_on": ["a"]},
    ]})
    assert result["layers"] == [["a"], ["b"]]
    with pytest.raises(WorkflowServiceError):
        service.validate({"name": "example", "steps": [{"key": "a", "goal": "research", "depend_on": ["x"]}]})
    with pytest.raises(WorkflowServiceError):
        service.validate({"name": "example", "steps": [{"key": "a", "goal": "research", "timeout": -1}]})
