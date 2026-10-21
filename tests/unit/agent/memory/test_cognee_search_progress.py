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
    projection_ready = True
    projection_namespace = ""
    def __init__(self):
        self.calls = []
        self.pending = 0
        self.cancelled = 0
        self.release = asyncio.Event()
        self.fail_graph = False

    async def initialize(self):
        return SimpleNamespace(ready=True)

    async def available_datasets(self, names):
        return names

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


async def test_explicit_dataset_scope_is_intersected_not_broadened():
    client = SearchClient()
    client.projection_namespace = "current"
    await search_cognee(client, CogneeConfig(), "query", ["current_relations"], 5, ["CHUNKS"])
    assert [call["datasets"] for call in client.calls] == [["current_relations"]]
    client.calls.clear()
    assert await search_cognee(client, CogneeConfig(), "query", ["old_global"], 5, ["CHUNKS"]) == []
    assert client.calls == []


async def test_partial_recall_reports_timeout_without_losing_native_evidence(monkeypatch):
    from agent.memory.cognee.fusion import RecallDiagnostics, federated_search
    from agent.memory.memory_types import MemorySearchResult

    monkeypatch.setattr("agent.memory.cognee.fusion.get_config_float", lambda *args: .1)
    client = SearchClient()
    diagnostic = RecallDiagnostics()

    async def native():
        return [MemorySearchResult(id="mem:1", snippet="原生证据", score=1)]

    found = await federated_search(native(), query="query", client=client,
        config=CogneeConfig(search_types=["CHUNKS", "GRAPH_COMPLETION"]), limit=5, diagnostics=diagnostic)
    assert "mem:1" in {result.id for result in found}
    assert diagnostic.result()["status"] == "partial" and diagnostic.timed_out
    assert diagnostic.native == "complete" and diagnostic.completed_lanes == 2
    assert client.pending == 0


async def test_strict_tags_exclude_unattributed_and_graph_answers(monkeypatch):
    from unittest.mock import AsyncMock

    from agent.memory.tag_query import TagQuery

    client = SearchClient()
    results = [
        CogneeRecallItem(id="valid", content="原文", metadata={"anelf_attribution": {"tags": ["future:a/b"]}}),
        CogneeRecallItem(id="unknown", content="没有出处"),
        CogneeRecallItem(id="wrong", content="其他项目", metadata={"anelf_attribution": {"tags": ["future:ab"]}}),
        CogneeRecallItem(id="graph", content="综合答案", source="cognee_graph",
                         metadata={"anelf_attribution": {"tags": ["future:a/b"]}}),
    ]
    recall = AsyncMock(return_value=results)
    monkeypatch.setattr(client, "recall", recall)
    found = await search_cognee(client, CogneeConfig(), "query", ["scope"], 5,
        ["CHUNKS", "GRAPH_COMPLETION"], tag_query=TagQuery(all_of=("future:a/*",)))
    assert [item.id for item in found] == ["valid"]
    assert recall.await_count == 1 and recall.call_args.kwargs["query_type"] == "CHUNKS"


@pytest.mark.parametrize("fresh", [False, True])
async def test_overview_uses_only_fresh_global_context(monkeypatch, fresh):
    from unittest.mock import AsyncMock

    client = SearchClient()
    monkeypatch.setattr(client, "global_context_ready", lambda dataset: fresh, raising=False)
    recall = AsyncMock(return_value=[])
    monkeypatch.setattr(client, "recall", recall)
    await search_cognee(client, CogneeConfig(), "query", ["scope"], 5, ["GRAPH_COMPLETION"], overview=True)
    options = recall.call_args.kwargs
    assert options["only_context"] is not fresh
    assert bool(options.get("retriever_specific_config", {}).get("include_global_context_index")) is fresh


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
        await hub.collect(scope=state.scope, plan=RetrievalPlan(queries=["query"], deep_needed=True), store=store)
        assert "已索引的项目部署事实" in state.rendered
        assert state.prepared.seen_result("memory:fast")
        assert not state.ledger.seen_result("memory:fast")
        hub.acknowledge(state.scope, state.rendered)
        assert state.ledger.seen_result("memory:fast")
        assert {call["query_type"] for call in client.calls} == {"CHUNKS", "GRAPH_COMPLETION"}
        assert all(call["only_context"] == (call["query_type"] == "GRAPH_COMPLETION") for call in client.calls)
        record = hook_journal.snapshot()["recent"][0]
        assert record["mode"] == "sync" and record["event"] == "context_prepare"
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


async def test_federation_publishes_native_and_fast_chunks_before_slow_graph():
    from agent.memory.cognee.fusion import federated_search
    from agent.memory.memory_types import MemorySearchResult

    client = SearchClient()
    snapshots = []
    ready = asyncio.Event()

    async def native():
        return [MemorySearchResult(id="native", snippet="native evidence", score=1)]

    def consume(results):
        snapshots.append(results)
        if {item.id for item in results} == {"native", "memory:fast"}:
            ready.set()

    task = asyncio.create_task(federated_search(
        native(), query="query", client=client,
        config=CogneeConfig(search_types=["CHUNKS", "GRAPH_COMPLETION"]),
        limit=5, on_results=consume,
    ))
    try:
        await asyncio.wait_for(ready.wait(), 1)
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert {item.id for item in snapshots[-1]} == {"native", "memory:fast"}
    assert client.pending == 0


async def test_query_embedding_single_flight_is_scoped_and_cancellation_safe():
    from agent.memory.cognee.adapters import _wrap_embed, query_embeddings

    calls = 0
    release = asyncio.Event()

    async def embed(text):
        nonlocal calls
        calls += 1
        await release.wait()
        return [[1.0, 2.0]]

    engine = SimpleNamespace(embed_text=embed)
    other = SimpleNamespace(embed_text=embed)
    _wrap_embed(engine)
    _wrap_embed(other)
    async with query_embeddings():
        first = asyncio.create_task(engine.embed_text(["same"]))
        second = asyncio.create_task(engine.embed_text(["same"]))
        third = asyncio.create_task(other.embed_text(["same"]))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        first.cancel()
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        assert await second == [[1.0, 2.0]]
        assert await third == [[1.0, 2.0]]
        assert calls == 2
    await engine.embed_text(["same"])
    assert calls == 3


async def test_chunk_results_keep_individual_identity_and_metadata(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock

    from agent.memory.cognee.client import CogneeClient
    from agent.memory.provenance import note_origin

    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    client.initialize = AsyncMock(return_value=SimpleNamespace(ready=True))
    client.available_datasets = AsyncMock(side_effect=lambda names: names)
    client.search_type = lambda name: name

    async def recall(_name, query, **kwargs):
        assert kwargs["only_context"] is False
        return [{"text": f"chunk-{i}: " + "x" * 900, "kind": "chunk", "source": "graph",
                 "metadata": {"chunk_id": f"id-{i}"},
                 "raw": {"external_metadata": {"anelf_memory_id": str(i), "anelf_attribution": {
                     "tags": [f"user:qq:{i}"], "provenance": note_origin(f"user_qq:{i}"),
                     "sensitivity": "private",
                 }}}} for i in range(3)]

    monkeypatch.setattr(client, "_call", recall)
    results = await search_cognee(client, client.config, "q", ["data"], 3)
    assert len(results) == 3
    assert {r.id for r in results} == {"cognee:id-0", "cognee:id-1", "cognee:id-2"}
    assert all(len(r.snippet) == 700 and r.source == "cognee_chunk" for r in results)
    assert {r.provenance["anelf_memory_id"] for r in results} == {"0", "1", "2"}
    assert {r.provenance["origins"][0]["scope"] for r in results} == {"user_qq:0", "user_qq:1", "user_qq:2"}
    assert all(r.sensitivity == "private" for r in results)


def test_rrf_counts_rank_per_lane_and_duplicate_once():
    from agent.memory.cognee.fusion import _fuse_ranked_lists
    from agent.memory.memory_types import MemorySearchResult

    a = MemorySearchResult(id="a", snippet="first", score=1)
    b = MemorySearchResult(id="b", snippet="second", score=1)
    config = CogneeConfig()
    results = _fuse_ranked_lists([([a, a, b], 1), ([b, a], 1)], config=config, limit=5)
    assert len(results) == 2
    assert results[0].score == results[1].score


def test_fusion_keeps_conflict_warning_when_native_source_wins():
    from agent.memory.cognee.fusion import reciprocal_rank_fusion
    from agent.memory.memory_types import MemorySearchResult

    native = MemorySearchResult(id="mem:7", snippet="原生事实", score=1)
    chunk = MemorySearchResult(id="cognee:7", snippet="原生事实", score=1, source="cognee_chunk",
        provenance={"anelf_memory_id": "7", "conflicts": [{"id": "conflict:1"}]})
    result = reciprocal_rank_fusion([native], [chunk], config=CogneeConfig(), limit=5)
    assert len(result) == 1 and result[0].source == "memory"
    assert result[0].provenance["conflicts"] == [{"id": "conflict:1"}]
    assert native.provenance == {}


@pytest.mark.parametrize("source, malformed", [("cognee_chunk", False), ("cognee_graph", False), ("cognee_chunk", True)])
async def test_chunk_metadata_restores_attribution_without_assigning_it_to_graph(source, malformed):
    from unittest.mock import AsyncMock

    from agent.memory.provenance import note_origin

    client = SearchClient()
    body = "只有正文的文档片段"
    client.recall = AsyncMock(return_value=[CogneeRecallItem(
        id="chunk:7", content=body, score=.8, source=source,
        metadata={"anelf_memory_id": "7", "anelf_attribution": {
            "tags": 1 if malformed else ["user:qq:1"], "timestamp": 1_700_000_000,
            "sensitivity": "private", "provenance": note_origin("group_qq:2"),
        }},
    )])
    results = await search_cognee(client, CogneeConfig(), "query", ["scope"], 5, ["CHUNKS"])
    assert len(results) == 1 and results[0].snippet == "只有正文的文档片段"
    result = results[0]
    assert "anelf_attribution" not in result.provenance
    if source == "cognee_chunk" and not malformed:
        assert result.tags == ["user:qq:1"] and result.sensitivity == "private"
        assert result.provenance["origins"][0]["scope"] == "group_qq:2"
        assert result.timestamp == 1_700_000_000 and result.memory_reference() == "mem:7"
    else:
        assert result.tags == [] and "origins" not in result.provenance
        if source == "cognee_graph":
            assert result.memory_reference() == ""
