"""并发检索增量、失败隔离与动态补充的预算收口。"""

import asyncio
from types import SimpleNamespace

import pytest

from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.fusion import CogneeSearchBatch, search_cognee
from agent.memory.cognee.types import CogneeRecallItem
from agent.memory.memory_types import RetrievalPlan
from agent.memory.probe import DeepProbeHub
from core.hooks import hook_journal


class SearchClient:
    def __init__(self):
        self.calls = []
        self.pending = 0
        self.cancelled = 0
        self.release = asyncio.Event()
        self.fail_graph = False

    async def initialize(self):
        return SimpleNamespace(ready=True)

    def search_type(self, name):
        return name

    async def recall(self, query, **kwargs):
        self.calls.append(kwargs)
        if kwargs["query_type"] == "GRAPH_COMPLETION":
            if self.fail_graph:
                raise TimeoutError("graph request deadline")
            self.pending += 1
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
            finally:
                self.pending -= 1
            return []
        return [CogneeRecallItem(id="memory:fast", content="已索引的项目部署事实", score=1)]


async def test_completed_lane_is_delivered_before_slow_lane_and_cancel_drains():
    client = SearchClient()
    received = asyncio.Event()
    batches = []

    async def consume(batch: CogneeSearchBatch):
        batches.append(batch)
        if batch.results:
            received.set()

    task = asyncio.create_task(search_cognee(
        client, CogneeConfig(enabled=True), "query", ["scope"], 5,
        ["CHUNKS", "GRAPH_COMPLETION"], on_batch=consume,
    ))
    try:
        await asyncio.wait_for(received.wait(), 1)
        assert not task.done()
        assert any(batch.lane == "CHUNKS/scope" and batch.status == "done" for batch in batches)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert client.pending == 0 and client.cancelled == 1


async def test_failed_lane_reports_cause_without_discarding_other_results():
    client = SearchClient()
    client.fail_graph = True
    batches = []

    async def consume(batch: CogneeSearchBatch):
        batches.append(batch)

    results = await search_cognee(client, CogneeConfig(enabled=True), "query", ["scope"], 5,
        ["CHUNKS", "GRAPH_COMPLETION"], on_batch=consume)
    assert results[0].snippet == "已索引的项目部署事实"
    failure = next(batch for batch in batches if batch.status == "error")
    assert failure.lane == "GRAPH_COMPLETION/scope"
    assert "TimeoutError" in failure.error and failure.elapsed >= 0


@pytest.mark.parametrize("timeout", [False, True])
async def test_probe_keeps_partial_results_and_reports_unfinished_stage(store, monkeypatch, timeout):
    import agent.memory.cognee.config as config_module
    import agent.memory.cognee.runtime as runtime_module
    import agent.memory.probe as probe_module

    client = SearchClient()
    client.fail_graph = not timeout
    monkeypatch.setattr(config_module, "load_cognee_config", lambda: CogneeConfig(enabled=True))
    monkeypatch.setattr(runtime_module, "get_cognee_client", lambda: client)
    monkeypatch.setattr(probe_module, "get_config_float", lambda key, default: 5)
    hub = DeepProbeHub()
    state = hub.begin_reply("user_webui:1")
    hook_journal.clear()
    try:
        await hub._run(state, RetrievalPlan(queries=["query"], deep_needed=True), store, state.revision)
        assert "已索引的项目部署事实" in state.rendered
        assert state.ledger.seen_result("memory:fast")
        assert {call["query_type"] for call in client.calls} == {"CHUNKS", "GRAPH_COMPLETION"}
        assert all(call["only_context"] for call in client.calls)
        record = hook_journal.snapshot()["recent"][0]
        assert record["status"] == "error"
        assert record["model_calls"] is None and record["tool_calls"] is None
        assert "已保留" in record["detail"]
        assert "GRAPH_COMPLETION" in record["detail"]
        if timeout:
            assert "预算 5 秒" in record["detail"]
        assert client.pending == 0
    finally:
        await hub.end_reply(state.scope)
        hook_journal.clear()


async def test_entity_resolution_alone_does_not_start_remote_deep_search(store, monkeypatch):
    import agent.memory.cognee.runtime as runtime_module

    def unexpected_client():
        raise AssertionError("The planner did not request deep retrieval")

    monkeypatch.setattr(runtime_module, "get_cognee_client", unexpected_client)
    hub = DeepProbeHub()
    state = hub.begin_reply("user_webui:1")
    await hub._probe(state, RetrievalPlan(queries=["query"], node_keys=["user:webui:1"]), store)
    assert not state.rendered
