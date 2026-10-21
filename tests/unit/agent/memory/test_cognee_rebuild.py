"""用真实 SQLite 验证构建、并发更新、断点恢复和后端验收边界。"""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import NAMESPACE_URL, uuid5

import pytest

from agent.memory.cognee.client import CogneeClient
from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.coordinator import CogneeCoordinator
from agent.memory.cognee.types import CogneeAvailability
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType


class DocumentBackend(CogneeClient):
    """只替换远端文档操作；队列、映射、快照和状态机使用生产实现。"""

    def __init__(self, config: CogneeConfig) -> None:
        super().__init__(config)
        self.documents: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> CogneeAvailability:
        return CogneeAvailability(installed=True, enabled=True, ready=True)

    async def make_data_item(self, data: str, *, label: str, external_metadata: dict[str, Any],
                             dataset_name: str = "", node_set: list[str] | None = None) -> Any:
        data_id = str(uuid5(NAMESPACE_URL, dataset_name + label + external_metadata["anelf_content_hash"]))
        return {"data_id": data_id, "id": data_id, "text": data, "external_metadata": external_metadata,
                "node_set": node_set or []}

    async def add(self, data: Any, **kwargs: Any) -> Any:
        name = kwargs["dataset_name"]
        dataset = self.documents.setdefault(name, {})
        for item in data:
            dataset[item["data_id"]] = copy.deepcopy(item)
        return {"dataset_id": name}

    async def cognify(self, **kwargs: Any) -> Any:
        return None

    async def list_datasets(self, **kwargs: Any) -> Any:
        return [{"id": name, "name": name} for name in self.documents]

    async def list_data(self, dataset_id: Any, **kwargs: Any) -> Any:
        return [{**item, "node_set": json.dumps(item.get("node_set", []))}
                for item in self.documents.get(str(dataset_id), {}).values()]

    async def delete_data(self, dataset_id: Any, data_id: Any, **kwargs: Any) -> Any:
        self.documents.get(str(dataset_id), {}).pop(str(data_id), None)

    def document_processed(self, item: Any, dataset_id: str) -> bool:
        return item.get("processed", True)


@pytest.fixture
async def coordinator(store: MemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("agent.memory.cognee.rebuild.backups_root", lambda: tmp_path / "backups")
    config = CogneeConfig(data_root=str(tmp_path / "cognee"), write_breaker_enabled=False)
    client = DocumentBackend(config)
    store.set_cognee_projection_enabled(True)
    value = CogneeCoordinator(store, client, config)
    await value.rebuild.initialize(config)
    return value


async def settle(coordinator: CogneeCoordinator) -> None:
    for _ in range(40):
        await coordinator.rebuild.advance()
        batch = await coordinator.store.claim_cognee_sync_batch(40)
        if batch:
            await coordinator._process_batch(batch)
        state = coordinator.rebuild.state
        if state and state.phase == "ready":
            return
        assert state is not None and state.phase != "failed", state
    pytest.fail("构建未在有限轮次内收敛")


async def test_restart_immediately_resumes_freshly_claimed_work(coordinator, store, monkeypatch):
    await store.add(MemoryEntry(content="中断后必须立即恢复的原文", memory_type=MemoryType.SEMANTIC))
    assert len(await store.claim_cognee_sync_batch(1)) == 1
    processed = asyncio.Event()
    original = coordinator._process_entries

    async def process(batch):
        await original(batch)
        processed.set()

    monkeypatch.setattr(coordinator, "_process_entries", process)
    try:
        await coordinator.start()
        await asyncio.wait_for(processed.wait(), timeout=3)
        status = await store.get_cognee_sync_status()
        assert status["pending"] == status["failed"] == 0
        assert status["synced"] == 1
    finally:
        await coordinator.close()


@pytest.mark.parametrize("legacy", [False, True])
async def test_full_build_preserves_sources_and_all_pages(coordinator, store, legacy):
    ids = [await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content=f"原文-{i}",
                                      tags=["project:alpha", "user:qq:1", "future:kind"]))
           for i in range(103)]
    archived = await store.add(MemoryEntry(content="归档原文", memory_type=MemoryType.EPISODIC))
    assert await store.archive_memory(archived)
    await store.add(MemoryEntry(content="未激活原文", importance=0, memory_type=MemoryType.SEMANTIC))
    await store.add(MemoryEntry(content="计划原文", source="goal", memory_type=MemoryType.SEMANTIC))
    await store.graph.add_relation("user:qq:1", "参与", "project:alpha", source_memory_id=ids[0], evidence="原始依据")
    db = await store._get_db()
    tables = ("memories", "memories_archive", "graph_nodes", "graph_edges")
    before = {name: [tuple(row) for row in await (await db.execute(f"SELECT * FROM {name} ORDER BY id")).fetchall()]
              for name in tables}
    if legacy:
        await coordinator._process_batch(await store.claim_cognee_sync_batch(200))
        # 旧的每实体数据集 + 上游迁移拆行，正文不靠旧 Cognee 格式解码恢复。
        documents = coordinator.client.documents
        mappings = await store.projection_state.mappings()
        for index, mapping in enumerate(mappings):
            item = documents[mapping["dataset_id"]].pop(mapping["data_id"])
            name = f"anelf_user_legacy_{index % 65}"
            old_id = item["id"]
            item.update(id="split-" + old_id, legacy_id=old_id, external_metadata={}, text="旧包装文本")
            documents.setdefault(name, {})[item["id"]] = item
            await db.execute("UPDATE cognee_entry_map SET dataset_id=?,dataset_name=?,content_hash='' WHERE entry_kind=? AND entry_id=?",
                             (name, name, mapping["entry_kind"], mapping["entry_id"]))
        await db.commit()
    await coordinator.rebuild.request(coordinator.config)
    assert not coordinator.client.projection_ready
    await settle(coordinator)
    state = coordinator.rebuild.state
    assert state.verified == 105 and state.counts["excluded_memories"] == 2
    assert coordinator.client.projection_ready
    assert (await store.projection_state.audit())["missing_or_stale"] == []
    for name in tables:
        assert [tuple(row) for row in await (await db.execute(f"SELECT * FROM {name} ORDER BY id")).fetchall()] == before[name]
    with sqlite3.connect(state.backup_path) as snapshot:
        assert snapshot.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert snapshot.execute("SELECT content FROM memories_archive WHERE id=?", (archived,)).fetchone() == ("归档原文",)
    for mapping in await store.projection_state.mappings():
        item = coordinator.client.documents[mapping["dataset_id"]][mapping["data_id"]]
        assert set(item["node_set"]) >= {"project:alpha", "user:qq:1", "future:kind"}
    old = copy.deepcopy(coordinator.client.documents)
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    for name, documents in old.items():
        assert coordinator.client.documents[name] == documents


@pytest.mark.parametrize("damage", ["missing", "tags", "hash", "unprocessed", "extra"])
async def test_backend_damage_never_activates_generation(coordinator, store, damage):
    await store.add(MemoryEntry(content="不可丢失", memory_type=MemoryType.SEMANTIC, tags=["future:one"]))
    await coordinator.rebuild.request(coordinator.config)
    await coordinator.rebuild.advance()
    await coordinator.rebuild.advance()
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    dataset = next(iter(coordinator.client.documents.values()))
    item = next(iter(dataset.values()))
    if damage == "missing":
        dataset.clear()
    elif damage == "tags":
        item["node_set"] = []
    elif damage == "hash":
        item["external_metadata"]["anelf_content_hash"] = "wrong"
    elif damage == "unprocessed":
        item["processed"] = False
    else:
        dataset["extra"] = {**item, "id": "extra"}
    await coordinator.rebuild.advance()
    assert coordinator.rebuild.state.phase == "failed"
    assert not coordinator.client.projection_ready
    assert await store.get(1) is not None
    namespace = coordinator.rebuild.state.namespace
    await coordinator.rebuild.request(coordinator.config)
    if damage == "extra":
        # 未知文档可能含独有内容，修复不能擅自删除它来凑齐计数。
        await coordinator.rebuild.advance()
        await coordinator.rebuild.advance()
        await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
        await coordinator.rebuild.advance()
        assert coordinator.rebuild.state.phase == "failed"
        assert "extra" in dataset
    else:
        await settle(coordinator)
        assert coordinator.rebuild.state.namespace == namespace


async def test_live_write_during_verification_delays_activation(coordinator, store, monkeypatch):
    await store.add(MemoryEntry(content="旧事实", memory_type=MemoryType.SEMANTIC))
    await coordinator.rebuild.request(coordinator.config)
    await coordinator.rebuild.advance()
    await coordinator.rebuild.advance()
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    original = coordinator.client.list_data

    async def write_then_list(dataset_id, **kwargs):
        entry = await store.get(1)
        entry.content, entry.tags = "核对期间新事实", ["new:tag"]
        await store.update(entry)
        return await original(dataset_id, **kwargs)

    monkeypatch.setattr(coordinator.client, "list_data", write_then_list)
    await coordinator.rebuild.advance()
    assert coordinator.rebuild.state.phase == "building"
    assert not coordinator.client.projection_ready
    monkeypatch.setattr(coordinator.client, "list_data", original)
    await settle(coordinator)
    mapping = await store.get_cognee_mapping(1)
    assert "核对期间新事实" in coordinator.client.documents[mapping["dataset_id"]][mapping["data_id"]]["text"]


async def test_unmapped_old_document_blocks_before_reset(coordinator, store):
    await store.add(MemoryEntry(content="原文", memory_type=MemoryType.SEMANTIC))
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    mappings = await store.projection_state.mappings()
    coordinator.client.documents["anelf_global"]["unmanaged"] = {"id": "unmanaged", "text": "独有旧数据"}
    await coordinator.rebuild.request(coordinator.config)
    await coordinator.rebuild.advance()
    assert coordinator.rebuild.state.phase == "failed"
    assert "1 个" in coordinator.rebuild.state.error
    assert await store.projection_state.mappings() == mappings
    assert not coordinator.rebuild.state.backup_path


async def test_mapped_document_without_preserved_source_blocks_rebuild(coordinator, store):
    mid = await store.add(MemoryEntry(content="仅存于旧索引的正文", memory_type=MemoryType.SEMANTIC))
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    db = await store._get_db()
    await db.execute("DELETE FROM memories WHERE id=?", (mid,))
    await db.commit()
    mappings = await store.projection_state.mappings()
    await coordinator.rebuild.request(coordinator.config)
    await coordinator.rebuild.advance()
    assert coordinator.rebuild.state.phase == "failed"
    assert await store.projection_state.mappings() == mappings
    assert coordinator.client.documents["anelf_global"]


async def test_request_is_durable_without_waiting_for_projection(coordinator, store):
    async with coordinator._projection_lock:
        result = await asyncio.wait_for(coordinator.request_rebuild(), 1)
    assert result["scheduled"]
    restarted = CogneeCoordinator(store, coordinator.client, coordinator.config)
    await restarted.rebuild.initialize(coordinator.config)
    assert restarted.rebuild.state.rebuild_requested
    await restarted.rebuild.request(coordinator.config)
    await restarted.rebuild.advance()
    namespace = restarted.rebuild.state.namespace
    restarted_again = CogneeCoordinator(store, coordinator.client, coordinator.config)
    await restarted_again.rebuild.initialize(coordinator.config)
    assert restarted_again.rebuild.state.namespace == namespace
    await settle(restarted_again)
    with pytest.raises(ValueError, match="不匹配"):
        restarted_again.rebuild.validate_config(CogneeConfig(data_root="other-directory"))


async def test_source_label_change_reprojects_relations_and_claim_is_atomic(coordinator, store):
    mid = await store.add(MemoryEntry(content="证据", memory_type=MemoryType.SEMANTIC, tags=["old:tag"]))
    await store.graph.add_relation("user:qq:1", "参与", "project:alpha", source_memory_id=mid)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    entry = await store.get(mid)
    entry.tags, entry.metadata = ["future:tag"], {"sensitivity": "secret"}
    await store.update(entry)
    first, second = await asyncio.gather(store.claim_cognee_sync_batch(20), store.claim_cognee_sync_batch(20))
    assert not {r["queue_id"] for r in first} & {r["queue_id"] for r in second}
    assert len(first + second) == 3
    await coordinator._process_batch(first + second)
    for row in await store.projection_state.mappings():
        item = coordinator.client.documents[row["dataset_id"]][row["data_id"]]
        assert "future:tag" in item["node_set"] and "old:tag" not in item["node_set"]
        assert item["external_metadata"]["anelf_attribution"]["sensitivity"] == "secret"


async def test_failed_ingestion_then_source_change_leaves_no_orphan(coordinator, store, monkeypatch):
    mid = await store.add(MemoryEntry(content="首个版本", memory_type=MemoryType.SEMANTIC))
    cognify = coordinator.client.cognify
    monkeypatch.setattr(coordinator.client, "cognify", AsyncMock(side_effect=RuntimeError("中断")))
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    assert len(await store.projection_state.staged()) == 1
    assert await store.get_cognee_mapping(mid) is None
    entry = await store.get(mid)
    entry.content = "随后更新版本"
    await store.update(entry)
    monkeypatch.setattr(coordinator.client, "cognify", cognify)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    assert await store.projection_state.staged() == []
    assert sum(map(len, coordinator.client.documents.values())) == 1
    assert "随后更新版本" in next(iter(coordinator.client.documents["anelf_global"].values()))["text"]


async def test_audit_does_not_block_writes_and_rejects_changed_revision(coordinator, store, monkeypatch):
    mid = await store.add(MemoryEntry(content="审计前原文", memory_type=MemoryType.SEMANTIC))
    await store.graph.add_relation("user:test:1", "负责", "project:a", source_memory_id=mid)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    entered, release = asyncio.Event(), asyncio.Event()
    render = store.graph.render_node_projection

    async def delayed(node_id):
        entered.set()
        await release.wait()
        return await render(node_id)

    monkeypatch.setattr(store.graph, "render_node_projection", delayed)
    audit_task = asyncio.create_task(store.projection_state.audit())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        entry = await store.get(mid)
        entry.content = "审计期间原文变化"
        await asyncio.wait_for(store.update(entry), 1)
    finally:
        release.set()
    audit = await audit_task
    assert not audit["consistent"]
    assert not await store.projection_state.finish(coordinator.rebuild.state, audit["expected"], revision=audit["revision"])
