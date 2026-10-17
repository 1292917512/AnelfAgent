"""工作流 Web / AI 双入口共享定义、快照与删除清理约束。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent.delegation.sub_agent import SubAgentResult
from agent.workflow.engine import WorkflowEngine
from agent.workflow.tools import (
    workflow_cleanup,
    workflow_definitions,
    workflow_delete,
    workflow_engine_port,
    workflow_save,
    workflow_start,
)
from core.path import ConfigPaths
from web.routers.workflow import router


@pytest.fixture
async def workflow_client(tmp_path, monkeypatch):
    monkeypatch.setattr(ConfigPaths, "WORKFLOW_DB", str(tmp_path / "workflow.sqlite3"))
    monkeypatch.setattr(ConfigPaths, "DELEGATION_DIR", str(tmp_path / "delegations"))
    manager = SimpleNamespace(delegate=AsyncMock(return_value=SubAgentResult(
        goal="work", success=True, output="result")))
    engine = WorkflowEngine(SimpleNamespace(delegation_manager=manager))
    monkeypatch.setattr("services.workflow.get_runtime", lambda: SimpleNamespace(workflow_engine=engine))
    monkeypatch.setattr(workflow_engine_port, "_box", [engine])
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, engine
    await engine.aclose()


async def test_web_and_ai_share_definition_crud_and_run_cleanup(workflow_client):
    client, engine = workflow_client
    spec = {"name": "saved", "steps": [{"key": "a", "goal": "work"}]}
    created = await client.post("/workflow/definitions", json={"spec": spec})
    assert created.status_code == 200
    workflow_id = created.json()["workflow_id"]
    assert not engine.running_ids()
    assert json.loads(await workflow_definitions())["definitions"][0]["workflow_id"] == workflow_id
    spec["name"] = "edited"
    assert json.loads(await workflow_save(json.dumps(spec), workflow_id))["ok"]
    assert (await client.get(f"/workflow/definitions/{workflow_id}")).json()["name"] == "edited"
    assert (await client.get("/workflow/definitions?offset=1")).json()["definitions"] == []
    invalid = await client.put(f"/workflow/definitions/{workflow_id}", json={"spec": {"name": "invalid", "steps": []}})
    assert invalid.status_code == 400
    assert (await client.get(f"/workflow/definitions/{workflow_id}")).json()["name"] == "edited"

    first = (await client.post("/workflow/runs", json={"workflow_id": workflow_id})).json()
    second = json.loads(await workflow_start(workflow_id=workflow_id))
    tasks = [state.task for state in engine._runs.values()]
    await asyncio.gather(*tasks)
    await asyncio.sleep(0)
    assert first["run_id"] != second["run_id"]
    assert json.loads(await workflow_delete(workflow_id=workflow_id))["ok"]
    assert (await client.get(f"/workflow/definitions/{workflow_id}")).status_code == 404
    assert (await client.get(f"/workflow/runs/{first['run_id']}")).json()["spec"]["name"] == "edited"
    assert (await client.delete(f"/workflow/runs/{first['run_id']}")).json()["ok"]
    assert (await client.get(f"/workflow/runs/{first['run_id']}")).status_code == 404
    assert json.loads(await workflow_cleanup(days=0))["deleted"] == 1
    assert (await client.get("/workflow/runs")).json()["runs"] == []
    assert (await client.post("/workflow/runs/cleanup", json={"days": -1})).status_code == 422
    assert (await client.post("/workflow/runs/cleanup", json={"days": 0})).json()["deleted"] == 0
    assert not json.loads(await workflow_delete(workflow_id="x", run_id="y")).get("ok", False)
    assert not json.loads(await workflow_save("bad json")).get("ok", False)


async def test_management_rejects_live_run_and_handles_unavailable_runtime(workflow_client, monkeypatch):
    client, engine = workflow_client
    await engine.journal.create_run("running", "live", "{}", "hash", "")
    assert (await client.delete("/workflow/runs/running")).status_code == 400
    assert (await client.post("/workflow/runs/cleanup", json={"days": 0, "include_stopped": True})).json()["deleted"] == 0
    assert not json.loads(await workflow_delete(run_id="running")).get("ok", False)
    monkeypatch.setattr("services.workflow._engine", lambda: None)
    monkeypatch.setattr(workflow_engine_port, "_box", [])
    assert (await client.get("/workflow/definitions")).status_code == 503
    assert (await client.delete("/workflow/runs/running")).status_code == 503
    assert not json.loads(await workflow_definitions()).get("ok", False)
