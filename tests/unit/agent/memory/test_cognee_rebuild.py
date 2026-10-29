"""用真实 SQLite 验证构建、并发更新、断点恢复和后端验收边界。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import NAMESPACE_URL, uuid5

import pytest

from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.coordinator import CogneeCoordinator
from agent.memory.cognee.sdk_client import SDKClient
from agent.memory.cognee.types import CogneeAvailability
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from tests.helpers.cognee import FakeIndexTransactions


class DocumentBackend(FakeIndexTransactions, SDKClient):
    """只替换远端文档操作；队列、映射、快照和状态机使用生产实现。"""

    def __init__(self, config: CogneeConfig) -> None:
        super().__init__(config)
        self.documents: dict[str, dict[str, Any]] = {}
        self.missing_graph: set[str] = set()

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

    async def delete_dataset(self, dataset_id: str) -> None:
        self.documents.pop(dataset_id, None)

    def document_processed(self, item: Any, dataset_id: str) -> bool:
        return item.get("processed", True)

    async def verify_graph_documents(self, dataset_id: str, data_ids: list[str]) -> None:
        if self.missing_graph.intersection(data_ids):
            raise RuntimeError("实际图谱缺少文档或分块关联")


@pytest.fixture
async def coordinator(store: MemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("agent.memory.cognee.rebuild.backups_root", lambda: tmp_path / "backups")
    monkeypatch.setattr("agent.memory.cognee.retirement.backups_root", lambda: tmp_path / "backups")
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


@pytest.mark.parametrize("side", ["subject", "object"])
async def test_relation_rename_requeues_existing_neighbors_even_with_inflight_projection(coordinator, store, side):
    await store.graph.add_relation("topic:hub", "连接", "topic:old", subject_label="旧名称")
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    old = await store.graph.get_node("topic:old")
    # 在途邻居仍持有旧名称快照；改名必须另外保留一条 pending，不能被旧批次 ACK 吃掉。
    await store.graph.add_relation("topic:hub", "连接", "topic:old", evidence="新增的在途证据")
    inflight = await store.claim_cognee_sync_batch(20)
    args = ("topic:hub", "连接", "topic:new") if side == "subject" else ("topic:new", "连接", "topic:hub")
    await store.graph.add_relation(*args, **{f"{side}_label": "新名称"})
    db = await store._conn.get_db()
    rows = await (await db.execute("SELECT status FROM cognee_sync_queue WHERE entry_kind='graph_node' AND entry_id=?",
                                   (old["id"],))).fetchall()
    assert {row["status"] for row in rows} == {"processing", "pending"}
    await coordinator._process_batch(inflight)
    batch = await store.claim_cognee_sync_batch(20)
    assert old["id"] in {item["entry_id"] for item in batch}
    await coordinator._process_batch(batch)
    assert (await store.projection_state.audit())["missing_or_stale"] == []
    mapping = await store.get_cognee_mapping(old["id"], entry_kind="graph_node")
    assert "新名称" in coordinator.client.documents[mapping["dataset_id"]][mapping["data_id"]]["text"]


async def _coverage_failure(coordinator, store):
    mid = await store.add(MemoryEntry(content="不得重复抽取的原文", memory_type=MemoryType.SEMANTIC))
    legacy_document(coordinator, mid, legacy_memory_text(mid, "旧轮保留的正文"))
    await store.graph.add_relation("topic:hub", "连接", "topic:a", subject_label="旧名称")
    await store.graph.add_relation("topic:hub", "连接", "topic:b")
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    state = coordinator.rebuild.state
    db = await store._conn.get_db()
    # 模拟升级前留下的改名漏队，不修改任何已提交文档或指纹。
    async with store._conn.tx(db):
        await db.execute("UPDATE graph_nodes SET label='新名称' WHERE node_key='topic:hub'")
    state.phase = "building"
    await coordinator.rebuild.advance()
    assert state.phase == "failed" and "权威覆盖不一致" in state.error
    return state


async def test_coverage_repair_resumes_same_generation_and_keeps_completed_documents(coordinator, store):
    state = await _coverage_failure(coordinator, store)
    before = await store.projection_state.mappings()
    preserved = copy.deepcopy(coordinator.client.documents)
    namespace, backup, previous = state.namespace, state.backup_path, list(state.previous_namespaces)
    coordinator.config.sync_enabled = False
    result = await coordinator.repair_coverage(namespace)
    assert result == {"queued": 3, "namespace": namespace, "paused": True}
    assert not coordinator.client.projection_ready and state.phase == "building"
    assert await store.projection_state.mappings() == before
    assert coordinator.client.documents == preserved
    batch = await store.claim_cognee_sync_batch(20)
    assert len(batch) == 3 and all(item["entry_kind"] == "graph_node" and not item["payload"].get("force") for item in batch)
    await store.requeue_stale_cognee_sync(0)
    # 入队持久化后重启，仍续同一轮次；最终验收才允许召回新图。
    await coordinator.rebuild.initialize(coordinator.config)
    coordinator.config.sync_enabled = True
    await settle(coordinator)
    state = coordinator.rebuild.state
    assert state.phase == "ready" and coordinator.client.projection_ready
    assert (state.namespace, state.backup_path, state.previous_namespaces) == (namespace, backup, previous)
    after = await store.projection_state.mappings()
    assert [m for m in after if m["entry_kind"] == "memory"] == [m for m in before if m["entry_kind"] == "memory"]
    assert (await store.get_cognee_sync_status())["pending"] == 0
    assert not (await store.projection_state.audit())["missing_or_stale"]
    with pytest.raises(ValueError, match="当前失败轮次"):
        await coordinator.repair_coverage(namespace)
    coordinator.config.sync_enabled = False
    current = copy.deepcopy(coordinator.client.documents)
    await coordinator.retirement.request_generation(previous[0])
    await coordinator.retirement._task
    assert coordinator.retirement.status.stage == "completed", coordinator.retirement.status.error
    assert coordinator.rebuild.state.previous_namespaces == []
    assert coordinator.client.projection_ready
    for name, documents in current.items():
        if name.startswith(namespace):
            assert coordinator.client.documents[name] == documents
    assert await store.projection_state.mappings() == after
    assert (await store.get(before[0]["entry_id"])).content == "不得重复抽取的原文"


async def test_coverage_repair_keeps_new_pending_work_and_still_requires_backend_validation(coordinator, store):
    state = await _coverage_failure(coordinator, store)
    mid = await store.add(MemoryEntry(content="验收失败后新增的记忆也不能丢", memory_type=MemoryType.SEMANTIC))
    result = await coordinator.repair_coverage(state.namespace)
    assert result["queued"] == 4  # 新增记忆缺少映射，同样属于实时覆盖差异。
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    mapping = await store.get_cognee_mapping(mid)
    coordinator.client.missing_graph.add(mapping["data_id"])
    await coordinator.rebuild.advance()
    assert state.phase == "failed" and not coordinator.client.projection_ready
    assert "图谱缺少" in state.error
    with pytest.raises(ValueError, match="未发现原生覆盖差异"):
        await coordinator.repair_coverage(state.namespace)
    assert (await store.get(mid)).content == "验收失败后新增的记忆也不能丢"


@pytest.mark.parametrize("block", ["revision", "generation", "requested", "inconsistent", "extra", "backend", "rollback", "cancel", "busy"])
async def test_coverage_repair_rejects_races_and_rolls_back_partial_enqueues(coordinator, store, monkeypatch, block):
    state = await _coverage_failure(coordinator, store)
    db = await store._conn.get_db()
    audit = await store.projection_state.audit()
    assert len(audit["missing_or_stale"]) == 3
    if block == "revision":
        async with store._conn.tx(db):
            await db.execute("UPDATE graph_nodes SET label='再次改名' WHERE node_key='topic:hub'")
    elif block == "generation":
        state.namespace += "_changed"
        await store.projection_state.save(state)
    elif block == "requested":
        await coordinator.request_rebuild()
    elif block == "inconsistent":
        audit["consistent"] = False
    elif block == "extra":
        audit["extra"] = ["graph_node:999"]
    elif block == "backend":
        audit["missing_or_stale"] = []
    elif block in {"rollback", "cancel"}:
        enqueue = store._cognee.enqueue_sync
        count = 0

        async def fail_after_insert(*args, **kwargs):
            nonlocal count
            await enqueue(*args, **kwargs)
            count += 1
            if count == 2:
                if block == "cancel":
                    raise asyncio.CancelledError()
                raise OSError("disk full")

        monkeypatch.setattr(store._cognee, "enqueue_sync", fail_after_insert)
    monkeypatch.setattr(store.projection_state, "audit", AsyncMock(return_value=audit))
    original = copy.deepcopy(state.model_dump())
    if block == "busy":
        await coordinator._projection_lock.acquire()
    try:
        error = asyncio.CancelledError if block == "cancel" else OSError if block == "rollback" else ValueError
        with pytest.raises(error):
            await coordinator.repair_coverage(state.namespace.removesuffix("_changed"))
    finally:
        if block == "busy":
            coordinator._projection_lock.release()
    assert (await store.get_cognee_sync_status())["pending"] == 0
    assert (await store.projection_state.load()).model_dump() == original
    assert state.model_dump() == original and not coordinator.client.projection_ready


async def test_large_node_fragments_preserve_old_index_and_update_only_changed_ranges(coordinator, store, monkeypatch):
    """旧文档不因启动改写；首次变更切分，随后修改、删除只替换受影响区间。"""
    from agent.memory.graph import fragments
    from agent.memory.projection_documents import mapped_documents

    hub = "topic:中心"
    evidence = {i: f"完整证据-{i}：" + "关系内容不能截断。" * 15 for i in range(70)}
    for i in range(70):
        await store.graph.add_relation(hub, "连接", f"topic:目标{i}", evidence=evidence[i])
    node_id = (await store.graph.get_node(hub))["id"]
    # 在同一个真实 SQLite 上模拟已有单文档部署，不改变源内容。
    with monkeypatch.context() as patch:
        patch.setattr(fragments, "MAX_DOCUMENT_CHARS", 1_000_000)
        await coordinator._process_batch(await store.claim_cognee_sync_batch(100))
    before = copy.deepcopy(coordinator.client.documents)
    mapping = await store.get_cognee_mapping(node_id, entry_kind="graph_node")
    assert not mapped_documents(mapping)
    await coordinator.rebuild.initialize(coordinator.config)
    assert coordinator.client.documents == before
    assert (await store.get_cognee_sync_status())["pending"] == 0
    # 仅强度变化也不会触发格式转换。
    await store.graph.add_relation(hub, "连接", "topic:目标1", evidence=evidence[1], strength=0.9)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(100))
    assert coordinator.client.documents == before

    added, deleted = [], []
    original_add, original_delete = coordinator.client.add, coordinator.client.delete_data

    async def add(data, **kwargs):
        added.extend(copy.deepcopy(data))
        return await original_add(data, **kwargs)

    async def delete(dataset_id, data_id, **kwargs):
        deleted.append(str(data_id))
        return await original_delete(dataset_id, data_id, **kwargs)

    monkeypatch.setattr(coordinator.client, "add", add)
    monkeypatch.setattr(coordinator.client, "delete_data", delete)
    evidence[1] += "补充证据。"
    await store.graph.add_relation(hub, "连接", "topic:目标1", evidence=evidence[1])
    await coordinator._process_batch(await store.claim_cognee_sync_batch(100))
    after = await store.get_cognee_mapping(node_id, entry_kind="graph_node")
    parts = mapped_documents(after)
    assert len(parts) > 2 and mapping["data_id"] in deleted
    source_docs = [coordinator.client.documents[after["dataset_id"]][part.data_id] for part in parts]
    assert all(len(doc["text"]) <= fragments.MAX_DOCUMENT_CHARS for doc in source_docs)
    for value in evidence.values():
        assert any(value in doc["text"] for doc in source_docs)
    # 各片仅携带所属边和来源，不能把全节点标签复制到每一片。
    assert max(len(doc["external_metadata"]["anelf_attribution"]["edges"]) for doc in source_docs) <= 32
    assert (await store.projection_state.audit())["missing_or_stale"] == []
    await coordinator.rebuild.verify_backend(coordinator.rebuild.state, await store.projection_state.mappings())

    unchanged = {part.part_key: part.data_id for part in parts}
    staged = AsyncMock(wraps=store.projection_state.stage)
    monkeypatch.setattr(store.projection_state, "stage", staged)
    added.clear()
    deleted.clear()
    evidence[1] += "再次修订。"
    await store.graph.add_relation(hub, "连接", "topic:目标1", evidence=evidence[1])
    await coordinator._process_batch(await store.claim_cognee_sync_batch(100))
    current = mapped_documents(await store.get_cognee_mapping(node_id, entry_kind="graph_node"))
    changed = {part.part_key for part in current if unchanged.get(part.part_key) != part.data_id}
    assert changed and all(key.startswith("edges-0-") for key in changed)
    reindexed_hub = [doc for doc in added if doc["external_metadata"]["anelf_graph_node_id"] == str(node_id)]
    assert len(reindexed_hub) == len(changed) < len(parts)
    kept = {part.data_id for part in current if part.part_key not in changed}
    assert not kept.intersection(row[3] for call in staged.await_args_list for row in call.args[0])
    assert set(deleted) & {part.data_id for part in current} == set()
    # 删除节点必须清理所有分片，并保留所有其他节点的有效映射。
    await store.graph.set_node_archived(hub, True)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(100))
    assert await store.get_cognee_mapping(node_id, entry_kind="graph_node") is None
    assert not ({part.data_id for part in current} & set(coordinator.client.documents[after["dataset_id"]]))
    assert (await store.projection_state.audit())["extra"] == []


@pytest.mark.parametrize("failure", ["prepare", "verify", "delete", "publish", "cancel"])
async def test_fragment_replacement_failure_keeps_mapping_and_all_prior_documents(coordinator, store, monkeypatch, failure):
    from agent.memory.graph import fragments

    monkeypatch.setattr(fragments, "MAX_DOCUMENT_CHARS", 400)
    await store.graph.add_relation("topic:甲", "连接", "topic:乙", evidence="必须保留的长证据。" * 80)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    before = copy.deepcopy(coordinator.client.documents)
    mappings = await store.projection_state.mappings()

    @asynccontextmanager
    async def transactional():
        snapshot = copy.deepcopy(coordinator.client.documents)
        try:
            yield
            if failure == "publish":
                raise OSError("publication failed")
        except BaseException:
            coordinator.client.documents = snapshot
            raise

    monkeypatch.setattr(coordinator.client, "write_transaction", transactional)
    if failure == "prepare":
        monkeypatch.setattr(coordinator.client, "prepare_deletions", AsyncMock(side_effect=RuntimeError("prepare failed")))
    if failure in {"verify", "cancel"}:
        async def stop(*args, **kwargs):
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("missing chunk")
        monkeypatch.setattr(coordinator.client, "verify_graph_documents", stop)
    if failure == "delete":
        original = coordinator.client.delete_data

        async def delete_then_fail(*args, **kwargs):
            await original(*args, **kwargs)
            raise OSError("delete failed after mutation")

        monkeypatch.setattr(coordinator.client, "delete_data", delete_then_fail)
    await store.graph.add_relation("topic:甲", "连接", "topic:乙", evidence="更新证据。" * 90)
    batch = await store.claim_cognee_sync_batch(10)
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await coordinator._process_batch(batch)
    else:
        await coordinator._process_batch(batch)
    assert coordinator.client.documents == before
    assert await store.projection_state.mappings() == mappings
    status = await store.get_cognee_sync_status()
    assert status["pending"] + status["failed"] == 2


def test_long_relation_document_split_is_lossless_and_bounded():
    from agent.memory.graph.fragments import MAX_DOCUMENT_CHARS, split_document

    header = "[关系节点] 甲（topic:甲，类型 topic）\n"
    body = "- 连接 → 乙——" + "证据🙂\n" * 5000 + "结尾必须保留"
    parts = split_document(header + body)
    assert len(parts) > 1 and max(map(len, parts)) <= MAX_DOCUMENT_CHARS
    assert "".join(part[len(header):] for part in parts) == body


async def test_publication_failure_never_acknowledges_queue_or_replaces_mapping(coordinator, store, monkeypatch):
    mid = await store.add(MemoryEntry(content="必须保留的权威正文", memory_type=MemoryType.SEMANTIC))

    @asynccontextmanager
    async def fail_at_publication():
        yield
        assert await store.get_cognee_mapping(mid) is None
        raise OSError("snapshot disk full")

    monkeypatch.setattr(coordinator.client, "write_transaction", fail_at_publication)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    assert await store.get_cognee_mapping(mid) is None
    assert (await store.get_cognee_sync_status())["pending"] == 1
    assert (await store.get(mid)).content == "必须保留的权威正文"
    assert await store.projection_state.staged("memory", mid)


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


async def test_pause_finishes_batch_and_survives_restart_until_resume(coordinator, store, monkeypatch, tmp_path):
    from agent.memory.cognee.config import load_cognee_config
    from core.path import ConfigPaths
    from services.memory import MemoryService

    monkeypatch.setattr(ConfigPaths, "COGNEE_CONFIG", str(tmp_path / "cognee.json"))
    coordinator.config.sync_batch_size = 1
    ids = [await store.add(MemoryEntry(content=f"暂停续跑 {i}", memory_type=MemoryType.SEMANTIC)) for i in range(2)]
    entered, release = asyncio.Event(), asyncio.Event()
    finished = asyncio.Event()
    original = coordinator.client.cognify
    calls = []

    async def blocking(**kwargs):
        calls.append(kwargs)
        entered.set()
        await release.wait()
        return await original(**kwargs)

    monkeypatch.setattr(coordinator.client, "cognify", blocking)
    monkeypatch.setattr(coordinator.client, "validate_configuration", Mock())
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: coordinator)
    from agent.memory.cognee.config import save_cognee_config
    save_cognee_config(coordinator.config)
    restarted = None
    try:
        await coordinator.start()
        await asyncio.wait_for(entered.wait(), 3)
        await MemoryService.save_cognee_config({"sync_enabled": False})
        assert not load_cognee_config().sync_enabled
        # 确认配置任务已排在投影锁之后，同时当前抽取仍然运行。
        await asyncio.sleep(0)
        assert (await coordinator.status()).pause_requested
        assert await store.get_cognee_mapping(ids[0]) is None
        release.set()
        await asyncio.wait_for(coordinator._configuration_task, 3)
        assert len(calls) == 1 and not coordinator.is_running
        assert (await store.get_cognee_sync_status()) == {"pending": 1, "failed": 0, "synced": 1}
        await coordinator.close()

        restarted = CogneeCoordinator(store, coordinator.client, load_cognee_config())
        await restarted.start()
        assert not restarted.is_running and not (await restarted.status()).enabled
        assert len(calls) == 1
        process = restarted._process_entries

        async def notify(batch):
            await process(batch)
            finished.set()

        monkeypatch.setattr(restarted, "_process_entries", notify)
        monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: restarted)
        await MemoryService.save_cognee_config({"sync_enabled": True})
        await asyncio.wait_for(finished.wait(), 3)
        assert len(calls) == 2
        assert (await store.get_cognee_sync_status()) == {"pending": 0, "failed": 0, "synced": 2}
        assert load_cognee_config().sync_enabled
    finally:
        release.set()
        await (restarted or coordinator).close()


@pytest.mark.parametrize("interruption", ["add", "cognify", "commit"])
async def test_rebuild_interruption_reopens_sqlite_and_resumes_same_generation(
    coordinator, store, monkeypatch, interruption,
):
    """在摄入、抽取和部分提交时取消；重新打开真实 SQLite 验证不丢源、不复制投影。"""
    ids = [await store.add(MemoryEntry(content=f"必须保留的原文 {i}", memory_type=MemoryType.SEMANTIC))
           for i in range(2)]
    await coordinator.rebuild.request(coordinator.config)
    await coordinator.rebuild.advance()
    namespace = coordinator.rebuild.state.namespace
    backup = coordinator.rebuild.state.backup_path
    entered = asyncio.Event()
    target = store if interruption == "commit" else coordinator.client
    method = "complete_cognee_sync" if interruption == "commit" else interruption
    original = getattr(target, method)

    async def interrupted(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await asyncio.Event().wait()
        return result

    monkeypatch.setattr(target, method, interrupted)
    task = asyncio.create_task(coordinator._process_batch(await store.claim_cognee_sync_batch(10)))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await coordinator.status()).active is None
    monkeypatch.setattr(target, method, original)
    documents_before = copy.deepcopy(coordinator.client.documents)
    await store.close()
    reopened = MemoryStore(store._db_path)
    reopened.set_cognee_projection_enabled(True)
    restarted = CogneeCoordinator(reopened, coordinator.client, coordinator.config)
    try:
        await restarted.rebuild.initialize(coordinator.config)
        assert restarted.rebuild.state.namespace == namespace
        assert restarted.rebuild.state.backup_path == backup and Path(backup).is_file()
        # 真正重新打开连接时 schema 初始化已经回收 processing；worker 的再次回收幂等。
        assert (await reopened.get_cognee_sync_status())["pending"] == (1 if interruption == "commit" else 2)
        assert await reopened.requeue_stale_cognee_sync(0) == 0
        await settle(restarted)
        assert restarted.rebuild.state.phase == "ready" and restarted.rebuild.state.verified == 2
        assert restarted.client.documents == documents_before
        assert await reopened.projection_state.staged() == []
        for i, mid in enumerate(ids):
            assert (await reopened.get(mid)).content == f"必须保留的原文 {i}"
            assert await reopened.get_cognee_mapping(mid) is not None
    finally:
        await reopened.close()


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


async def test_retry_clears_stale_failure_without_activating_pending_generation(coordinator, store):
    await store.add(MemoryEntry(content="待重试的原文", memory_type=MemoryType.SEMANTIC))
    await coordinator.rebuild.request(coordinator.config)
    await coordinator.rebuild.advance()
    await coordinator.rebuild.advance()
    batch = await store.claim_cognee_sync_batch(10)
    await store.fail_cognee_sync(batch[0]["queue_id"], "验收失败", max_retries=1, retry_delay_seconds=0)
    await coordinator.rebuild.advance()
    assert "1 条投影失败" in coordinator.rebuild.state.error
    namespace = coordinator.rebuild.state.namespace
    assert await coordinator.retry_failed() == 1
    await coordinator.rebuild.advance()
    persisted = await store.projection_state.load()
    assert persisted.phase == "building" and persisted.error == ""
    assert persisted.namespace == namespace and not coordinator.client.projection_ready
    await settle(coordinator)
    assert coordinator.rebuild.state.phase == "ready"
    assert coordinator.rebuild.state.namespace == namespace


async def test_rebuild_verifies_sdk_collisions_without_losing_source_tags(coordinator, store):
    tags = ["topic:WOL", "topic:WoL"]
    mid = await store.add(MemoryEntry(content="完整标签的原文", memory_type=MemoryType.SEMANTIC, tags=tags))
    await coordinator.rebuild.request(coordinator.config)
    await coordinator.rebuild.advance()
    await coordinator.rebuild.advance()
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    dataset = next(iter(coordinator.client.documents.values()))
    item = next(iter(dataset.values()))
    item["node_set"] = ["topic:WOL"]
    await coordinator.rebuild.advance()
    assert coordinator.rebuild.state.phase == "ready"
    assert item["external_metadata"]["anelf_attribution"]["tags"] == tags
    assert (await store.get(mid)).tags == tags


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


def legacy_document(coordinator, mid, text, *, graph=False):
    """真实文件承载旧文档，预检走生产路径解析及读取边界。"""
    data_id = str(uuid5(NAMESPACE_URL, text))
    path = Path(coordinator.config.absolute_data_root) / "data" / f"旧文档-{data_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    kind = "graph-node" if graph else "memory"
    item = {"id": data_id, "label": f"anelf-{kind}-{mid}", "raw_data_location": path.as_uri(),
            "external_metadata": {"anelf_graph_node_id" if graph else "anelf_memory_id": str(mid)},
            "node_set": '["future:tag"]'}
    coordinator.client.documents.setdefault("anelf_legacy", {})[data_id] = item
    return item


def legacy_memory_text(mid, content, *, with_identity=False):
    identity = f"Memory ID: {mid}\nTimestamp: 123.456\n" if with_identity else ""
    return f'Memory type: semantic\n{identity}Source: \nImportance: 0.7\nTags: future:tag\nMetadata: {{}}\n\n{content}'


@pytest.mark.parametrize("failure", ["none", "coverage", "unknown", "backup", "delete", "partial", "cancel"])
async def test_retirement_preserves_before_delete_and_retries_without_loss(coordinator, store, monkeypatch, failure):
    mid = await store.add(MemoryEntry(content="当前权威内容", memory_type=MemoryType.SEMANTIC))
    text = legacy_memory_text(mid, "旧数据集的独有历史正文")
    item = legacy_document(coordinator, mid, text)
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    coordinator.config.sync_enabled = False
    current = copy.deepcopy(coordinator.client.documents[coordinator.rebuild.state.namespace + "_global"])
    backup, delete = store.projection_state.backup, coordinator.client.delete_dataset
    if failure == "coverage":
        coordinator.client.documents[coordinator.rebuild.state.namespace + "_global"].clear()
    elif failure == "unknown":
        coordinator.client.documents["anelf_legacy"]["unknown"] = {"id": "unknown", "label": "external"}
    elif failure == "backup":
        monkeypatch.setattr(store.projection_state, "backup", AsyncMock(side_effect=OSError("disk full")))
    elif failure == "delete":
        monkeypatch.setattr(coordinator.client, "delete_dataset", AsyncMock(side_effect=RuntimeError("SDK unavailable")))
    elif failure == "partial":
        async def partial_delete(dataset_id):
            coordinator.client.documents[dataset_id].clear()
            raise RuntimeError("SDK interrupted after deleting document rows")

        monkeypatch.setattr(coordinator.client, "delete_dataset", partial_delete)
    elif failure == "cancel":
        # 取消发生在完整归档和备份之后；SDK 未完成，允许按同一目标重试。
        entered = asyncio.Event()

        async def wait_delete(_id):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(coordinator.client, "delete_dataset", wait_delete)
    result = await coordinator.retirement.request("anelf_legacy")
    assert result.running
    with pytest.raises(ValueError, match="清理正在执行"):
        await coordinator.request_rebuild()
    if failure == "cancel":
        await asyncio.wait_for(entered.wait(), 3)
        await coordinator.retirement.close()
    else:
        await coordinator.retirement._task
    status = coordinator.retirement.status
    if failure != "none":
        assert status.stage == ("interrupted" if failure == "cancel" else "failed")
        if failure != "partial":
            assert coordinator.client.documents["anelf_legacy"][item["id"]] == item
        coordinator.client.documents["anelf_legacy"].pop("unknown", None)
        coordinator.client.documents[coordinator.rebuild.state.namespace + "_global"] = copy.deepcopy(current)
        monkeypatch.setattr(store.projection_state, "backup", backup)
        monkeypatch.setattr(coordinator.client, "delete_dataset", delete)
        await coordinator.retirement.request("anelf_legacy")
        await coordinator.retirement._task
        status = coordinator.retirement.status
    assert status.stage == "completed" and not status.running
    assert "anelf_legacy" not in coordinator.client.documents
    assert coordinator.client.documents[coordinator.rebuild.state.namespace + "_global"] == current
    assert (await store.get(mid)).content == "当前权威内容"
    with sqlite3.connect(status.backup_path) as snapshot:
        assert snapshot.execute("PRAGMA quick_check").fetchone() == ("ok",)
        documents = [json.loads(row[0]) for row in snapshot.execute("SELECT document_json FROM cognee_legacy_documents")]
    # 预检与清理都留存同一文档，内容相同不重复存储。
    assert len(documents) == 1 and documents[0]["text"] == text
    assert documents[0]["external_metadata"]["anelf_memory_id"] == str(mid)
    assert "future:tag" in documents[0]["node_set"]
    assert (await store.projection_state.load()).previous_namespaces == []


async def test_retirement_rejects_active_external_and_unsynced_sources(coordinator, store):
    mid = await store.add(MemoryEntry(content="权威内容", memory_type=MemoryType.SEMANTIC))
    legacy_document(coordinator, mid, legacy_memory_text(mid, "旧正文"))
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    with pytest.raises(ValueError, match="暂停同步"):
        await coordinator.retirement.request("anelf_legacy")
    coordinator.config.sync_enabled = False
    for name in (coordinator.rebuild.state.namespace + "_global", "external_global", "missing"):
        with pytest.raises(ValueError, match="只能清理"):
            await coordinator.retirement.request(name)
    # 读完目录之后 guard 必须再检查；不能只依赖按钮状态。
    await store.add(MemoryEntry(content="尚未同步的新内容", memory_type=MemoryType.SEMANTIC))
    await coordinator.retirement.request("anelf_legacy")
    await coordinator.retirement._task
    assert coordinator.retirement.status.stage == "failed"
    assert "待同步" in coordinator.retirement.status.error
    assert "anelf_legacy" in coordinator.client.documents


async def test_retirement_preserves_current_format_old_generation(coordinator, store):
    from agent.memory.projection import PROJECTION_VERSION

    mid = await store.add(MemoryEntry(content="原文", memory_type=MemoryType.SEMANTIC))
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    # 旧代际也可能由当前投影格式生成；原文与元数据仍须完整归档。
    item = legacy_document(coordinator, mid, "当前格式的旧修订正文")
    item["external_metadata"].update(anelf_projection_version=PROJECTION_VERSION, anelf_content_hash="old-hash")
    coordinator.config.sync_enabled = False
    await coordinator.retirement.request("anelf_legacy")
    await coordinator.retirement._task
    status = coordinator.retirement.status
    assert status.stage == "completed", status.error
    with sqlite3.connect(status.backup_path) as snapshot:
        saved = json.loads(snapshot.execute("SELECT document_json FROM cognee_legacy_documents").fetchone()[0])
    assert saved["text"] == "当前格式的旧修订正文"
    assert saved["external_metadata"]["anelf_projection_version"] == PROJECTION_VERSION


async def test_unmapped_history_is_preserved_before_backup_and_retry_is_idempotent(coordinator, store):
    mid = await store.add(MemoryEntry(content="当前事实", memory_type=MemoryType.SEMANTIC))
    archived = await store.add(MemoryEntry(content="已归档事实", memory_type=MemoryType.SEMANTIC))
    await store.archive_memory(archived)
    await store.graph.add_relation("topic:alpha", "参与", "topic:beta", evidence="当前关系证据")
    node = await store.graph.get_node("topic:alpha")
    texts = [legacy_memory_text(mid, "当前事实"), legacy_memory_text(mid, "更新前的独有正文", with_identity=True),
             legacy_memory_text(archived, "已归档事实"),
             "[关系节点] 旧显示名（topic:alpha，类型 topic）\n- 参与 → beta（topic:beta）（强度 0.60）——旧证据"]
    for index, text in enumerate(texts):
        legacy_document(coordinator, node["id"] if index == 3 else archived if index == 2 else mid,
                        text, graph=index == 3)
    old = copy.deepcopy(coordinator.client.documents)
    # 首次预检在留存后中断，重试不能制造历史副本或清理其他未知内容。
    coordinator.client.documents["anelf_legacy"]["unknown"] = {"id": "unknown", "text": "外来独有内容"}
    await coordinator.rebuild.request(coordinator.config)
    await coordinator.rebuild.advance()
    assert coordinator.rebuild.state.phase == "failed"
    assert not coordinator.rebuild.state.backup_path
    coordinator.client.documents["anelf_legacy"].pop("unknown")
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    state = coordinator.rebuild.state
    assert state.legacy_preserved == 4
    assert (await store.get(mid)).content == "当前事实"
    assert (await store.resolve(archived))["status"] == "archived"
    assert coordinator.client.documents["anelf_legacy"] == old["anelf_legacy"]
    with sqlite3.connect(state.backup_path) as snapshot:
        rows = snapshot.execute("SELECT classification,document_json,snapshot_hash FROM cognee_legacy_documents").fetchall()
    assert len(rows) == 4
    assert {kind for kind, _, _ in rows} == {"matching_memory", "historical_memory", "historical_graph_node"}
    assert {json.loads(payload)["text"] for _, payload, _ in rows} == set(texts)
    for _, payload, digest in rows:
        document = json.loads(payload)
        assert hashlib.sha256(payload.encode()).hexdigest() == digest
        assert hashlib.sha256(document["text"].encode()).hexdigest() == document["text_sha256"]
    new_texts = [item["text"] for name, items in coordinator.client.documents.items() if name != "anelf_legacy"
                 for item in items.values()]
    assert "更新前的独有正文" not in new_texts and "已归档事实" not in new_texts


@pytest.mark.parametrize("damage", ["label", "identity", "header_identity", "format", "missing", "outside", "new_version", "write_failure"])
async def test_unverified_legacy_document_never_resets_mappings(coordinator, store, tmp_path, damage):
    mid = await store.add(MemoryEntry(content="当前事实", memory_type=MemoryType.SEMANTIC))
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    item = legacy_document(coordinator, mid, legacy_memory_text(mid, "旧正文", with_identity=True))
    db = await store._get_db()
    if damage == "label":
        item["label"] = "external-document"
    elif damage == "identity":
        item["external_metadata"]["anelf_memory_id"] = str(mid + 1)
    elif damage in {"format", "missing", "header_identity"}:
        path = next((Path(coordinator.config.absolute_data_root) / "data").glob("*.txt"))
        if damage == "missing":
            path.unlink()
        else:
            text = legacy_memory_text(mid + 1, "错误身份", with_identity=True) if damage == "header_identity" else "独有正文，无旧投影包装"
            path.write_text(text, encoding="utf-8")
    elif damage == "outside":
        path = tmp_path / "outside.txt"
        path.write_text(legacy_memory_text(mid, "越界正文"), encoding="utf-8")
        item["raw_data_location"] = path.as_uri()
    elif damage == "new_version":
        item["external_metadata"]["anelf_projection_version"] = 2
    else:
        await db.execute("CREATE TRIGGER fail_preserve BEFORE INSERT ON cognee_legacy_documents "
                         "BEGIN SELECT RAISE(ABORT, 'storage full'); END")
        await db.commit()
    mappings = await store.projection_state.mappings()
    await coordinator.rebuild.request(coordinator.config)
    await coordinator.rebuild.advance()
    assert coordinator.rebuild.state.phase == "failed"
    assert not coordinator.rebuild.state.backup_path
    assert await store.projection_state.mappings() == mappings
    assert (await store.get(mid)).content == "当前事实"
    assert (await (await db.execute("SELECT COUNT(*) FROM cognee_legacy_documents")).fetchone())[0] == 0


async def test_tombstone_uses_original_memory_id_and_does_not_resurrect_it(coordinator, store):
    db = await store._get_db()
    await db.execute("INSERT INTO memories_tombstone(id,memory_id,type,gist,purged_at_ns) VALUES(1,42,'semantic','旧梗概',1)")
    await db.commit()
    sources = await store.projection_state.preserved_sources()
    assert ("memory", 42) in sources and ("memory", 1) not in sources
    text = legacy_memory_text(42, "墓碑以外仍存在于旧投影的完整正文")
    legacy_document(coordinator, 42, text)
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    assert await store.get(42) is None
    assert coordinator.rebuild.state.legacy_preserved == 1
    row = await (await db.execute("SELECT document_json FROM cognee_legacy_documents")).fetchone()
    assert json.loads(row[0])["text"] == text


@pytest.mark.parametrize("delete_before_rebuild", [True, False])
async def test_retirement_preserves_hard_deleted_memory_with_audit(coordinator, store, delete_before_rebuild):
    mid = await store.add(MemoryEntry(content="待删除的旧事实", memory_type=MemoryType.ENTITY))
    retained = await store.add(MemoryEntry(content="新图当前事实", memory_type=MemoryType.SEMANTIC))
    text = legacy_memory_text(mid, "旧索引独有的完整历史正文", with_identity=True)
    legacy_document(coordinator, mid, text)
    if delete_before_rebuild:
        assert await store.delete(mid, actor="web")
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    if not delete_before_rebuild:
        assert await store.delete(mid, actor="web")
        await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    coordinator.config.sync_enabled = False
    mappings = await store.projection_state.mappings()
    namespace = coordinator.rebuild.state.namespace
    current = copy.deepcopy(coordinator.client.documents[namespace + "_global"])
    await coordinator.retirement.request_generation("anelf")
    await coordinator.retirement._task
    status = coordinator.retirement.status
    assert status.stage == "completed", status.error
    assert await store.get(mid) is None
    assert (await store.get(retained)).content == "新图当前事实"
    assert await store.projection_state.mappings() == mappings
    assert coordinator.client.documents[namespace + "_global"] == current
    assert "anelf_legacy" not in coordinator.client.documents
    with sqlite3.connect(status.backup_path) as snapshot:
        saved = [json.loads(row[0]) for row in snapshot.execute(
            "SELECT document_json FROM cognee_legacy_documents WHERE entry_id=?", (mid,))]
    deleted = [row for row in saved if row.get("source_status") == "deleted"]
    assert len(deleted) == 1 and deleted[0]["text"] == text
    assert deleted[0]["deletion_audit"]["actor"] == "web"
    assert (await store.projection_state.load()).previous_namespaces == []


@pytest.mark.parametrize("damage", ["no_audit", "later_event", "format", "identity", "audit_removed_during_read"])
async def test_deleted_history_still_requires_proof_and_complete_archive(coordinator, store, monkeypatch, damage):
    from agent.memory.cognee.preflight import preserve_owned_document

    mid = await store.add(MemoryEntry(content="已删除事实", memory_type=MemoryType.SEMANTIC))
    item = legacy_document(coordinator, mid, legacy_memory_text(mid, "完整历史", with_identity=True))
    assert await store.delete(mid)
    db = await store._get_db()
    if damage == "no_audit":
        await db.execute("DELETE FROM memory_audit WHERE memory_id=?", (mid,))
    elif damage == "later_event":
        await store._record_audit(db, mid, "restore")
    elif damage == "format":
        item = legacy_document(coordinator, mid, "缺少包装的未知内容")
    elif damage == "identity":
        item["external_metadata"]["anelf_memory_id"] = str(mid + 1)
    await db.commit()
    if damage == "audit_removed_during_read":
        preserve = store.projection_state.preserve_legacy_document

        async def remove_proof(**kwargs):
            await db.execute("DELETE FROM memory_audit WHERE memory_id=?", (mid,))
            await db.commit()
            await preserve(**kwargs)

        monkeypatch.setattr(store.projection_state, "preserve_legacy_document", remove_proof)
        with pytest.raises(RuntimeError, match="原生来源已消失"):
            await preserve_owned_document(store.projection_state, coordinator.rebuild.state,
                                          item, "anelf_legacy", "anelf_legacy", include_current_format=True)
    else:
        assert not await preserve_owned_document(store.projection_state, coordinator.rebuild.state,
                                                item, "anelf_legacy", "anelf_legacy", include_current_format=True)
    assert (await (await db.execute("SELECT COUNT(*) FROM cognee_legacy_documents")).fetchone())[0] == 0
    assert coordinator.client.documents["anelf_legacy"][item["id"]] == item
    assert await store.get(mid) is None


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


async def test_moved_index_rebinds_only_location_after_backend_verification(coordinator, store, tmp_path):
    mid = await store.add(MemoryEntry(content="迁移后仍存在的记忆", memory_type=MemoryType.SEMANTIC))
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    pending = await store.add(MemoryEntry(content="迁移后继续同步", memory_type=MemoryType.SEMANTIC))
    before = await store.projection_state.load()
    mappings = await store.projection_state.mappings()
    activity = await store.get_cognee_sync_activity()
    old_root = Path(coordinator.config.absolute_data_root)
    old_root.mkdir(parents=True)
    (old_root / "kept-index").write_bytes(b"unchanged")
    moved = tmp_path / "moved-cognee"
    old_root.rename(moved)
    config = replace(coordinator.config, data_root=str(moved))
    coordinator.client.reconfigure(config)
    restarted = CogneeCoordinator(store, coordinator.client, config)

    await restarted.rebuild.initialize(config)
    after = await store.projection_state.load()
    assert after.model_dump() == {**before.model_dump(), "root": str(moved)}
    assert await store.projection_state.mappings() == mappings
    assert await store.get_cognee_sync_activity() == activity
    assert (await store.get(mid)).content == "迁移后仍存在的记忆"
    assert (await store.get(pending)).content == "迁移后继续同步"
    assert (moved / "kept-index").read_bytes() == b"unchanged"
    await restarted.rebuild.initialize(config)
    assert (await store.projection_state.load()).model_dump() == after.model_dump()


@pytest.mark.parametrize("damage", ["missing", "fingerprint", "graph", "prefix", "unmapped"])
async def test_moved_index_never_rebinds_an_unverified_backend(coordinator, store, tmp_path, damage):
    mid = await store.add(MemoryEntry(content="不能丢失的原生记忆", memory_type=MemoryType.SEMANTIC))
    if damage != "unmapped":
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        mapping = await store.get_cognee_mapping(mid)
        if damage == "missing":
            coordinator.client.documents.clear()
        elif damage == "fingerprint":
            coordinator.client.documents[mapping["dataset_id"]][mapping["data_id"]]["external_metadata"][
                "anelf_content_hash"
            ] = "another-project"
        elif damage == "graph":
            coordinator.client.missing_graph.add(mapping["data_id"])
    else:
        coordinator.rebuild.state.phase = "required"
        await store.projection_state.save(coordinator.rebuild.state)
    before = (await store.projection_state.load()).model_dump()
    activity = await store.get_cognee_sync_activity()
    mappings = await store.projection_state.mappings()
    moved = tmp_path / "unverified-index"
    moved.mkdir()
    config = replace(coordinator.config, data_root=str(moved),
                     dataset_prefix="other" if damage == "prefix" else coordinator.config.dataset_prefix)
    coordinator.client.reconfigure(config)
    restarted = CogneeCoordinator(store, coordinator.client, config)
    with pytest.raises((ValueError, RuntimeError)):
        await restarted.rebuild.initialize(config)
    assert (await store.projection_state.load()).model_dump() == before
    assert await store.projection_state.mappings() == mappings
    assert await store.get_cognee_sync_activity() == activity
    assert (await store.get(mid)).content == "不能丢失的原生记忆"


@pytest.mark.parametrize("foreign_documents", [False, True])
async def test_unpublished_projection_moves_only_to_an_empty_backend(coordinator, store, tmp_path, foreign_documents):
    mid = await store.add(MemoryEntry(content="尚待首次同步的记忆", memory_type=MemoryType.SEMANTIC))
    before = (await store.projection_state.load()).model_dump()
    activity = await store.get_cognee_sync_activity()
    if foreign_documents:
        coordinator.client.documents["foreign-dataset"] = {"foreign": {"id": "foreign"}}
    config = replace(coordinator.config, data_root=str(tmp_path / "empty-moved-index"))
    coordinator.client.reconfigure(config)
    restarted = CogneeCoordinator(store, coordinator.client, config)
    if foreign_documents:
        with pytest.raises(ValueError, match="未知文档"):
            await restarted.rebuild.initialize(config)
        assert (await store.projection_state.load()).model_dump() == before
    else:
        await restarted.rebuild.initialize(config)
        assert (await store.projection_state.load()).model_dump() == {**before, "root": config.absolute_data_root}
    assert await store.get_cognee_sync_activity() == activity
    assert (await store.get(mid)).content == "尚待首次同步的记忆"


async def test_fresh_rebuild_survives_restart_and_repairs_missing_graph(coordinator, store):
    mid = await store.add(MemoryEntry(content="必须保留的唯一事实", memory_type=MemoryType.SEMANTIC))
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    old_namespace = coordinator.rebuild.state.namespace
    old_mapping = await store.get_cognee_mapping(mid)
    coordinator.client.missing_graph.add(old_mapping["data_id"])
    with pytest.raises(RuntimeError, match="实际图谱缺少"):
        await coordinator.rebuild.verify_backend(coordinator.rebuild.state, await store.projection_state.mappings())
    # 即使 SQL 标记已完成，也不能批准删除旧数据集。
    coordinator.config.sync_enabled = False
    coordinator.client.documents["anelf_legacy"] = {}
    await coordinator.retirement.request("anelf_legacy")
    await coordinator.retirement._task
    assert coordinator.retirement.status.stage == "failed"
    assert "anelf_legacy" in coordinator.client.documents
    coordinator.rebuild.state.phase = "building"
    await coordinator.request_rebuild(restart=True)
    assert not coordinator.config.sync_enabled
    coordinator.config.sync_enabled = True
    restarted = CogneeCoordinator(store, coordinator.client, coordinator.config)
    await restarted.rebuild.initialize(coordinator.config)
    assert restarted.rebuild.state.rebuild_from_scratch
    await restarted.rebuild.request(coordinator.config)
    await settle(restarted)
    assert restarted.rebuild.state.namespace != old_namespace
    assert old_namespace in restarted.rebuild.state.previous_namespaces
    assert not restarted.rebuild.state.rebuild_from_scratch
    assert (await store.get(mid)).content == "必须保留的唯一事实"
    assert old_mapping["dataset_id"] in coordinator.client.documents


async def test_shutdown_cancels_extraction_and_retries_uncommitted_batch(coordinator, store, monkeypatch):
    ids = [await store.add(MemoryEntry(content=f"停机保留 {i}", memory_type=MemoryType.SEMANTIC)) for i in range(2)]
    started, cancelled, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = coordinator.client.cognify

    async def extracting(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(coordinator.client, "cognify", extracting)
    await coordinator.start()
    await asyncio.wait_for(started.wait(), 3)
    coordinator.quiesce()
    await asyncio.wait_for(coordinator.close(), 3)
    assert cancelled.is_set()
    assert (await store.get_cognee_sync_status()) == {"pending": 2, "failed": 0, "synced": 0}
    for mid in ids:
        assert await store.get(mid) is not None
    monkeypatch.setattr(coordinator.client, "cognify", original)
    restarted = CogneeCoordinator(store, coordinator.client, coordinator.config)
    process = restarted._process_entries

    async def notify(batch):
        await process(batch)
        finished.set()

    monkeypatch.setattr(restarted, "_process_entries", notify)
    try:
        await restarted.start()
        await asyncio.wait_for(finished.wait(), 3)
        assert (await store.get_cognee_sync_status()) == {"pending": 0, "failed": 0, "synced": 2}
    finally:
        await restarted.close()


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


@pytest.mark.parametrize("failure", ["cognify", "verify", "publish"])
@pytest.mark.parametrize("bounded", [False, True])
async def test_retry_groups_keep_healthy_commits_across_failure_and_restart(coordinator, store, monkeypatch, failure, bounded):
    ids = [await store.add(MemoryEntry(content=f"旧事实 {i}", memory_type=MemoryType.SEMANTIC)) for i in range(8)]
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    previous = {mid: await store.get_cognee_mapping(mid) for mid in ids}
    client = coordinator.client
    original_add, original_verify = client.add, client.verify_graph_documents
    groups: list[list[int]] = []
    active: list[int] = []

    async def add(data, **kwargs):
        active[:] = [int(row["external_metadata"]["anelf_memory_id"]) for row in data]
        groups.append(active.copy())
        progress = await coordinator.writer.progress.snapshot(AsyncMock())
        assert progress.items == len(active)
        return await original_add(data, **kwargs)

    async def cognify(**kwargs):
        if failure == "cognify" and ids[0] in active:
            raise TimeoutError("one source unavailable")

    async def verify(*args, **kwargs):
        if failure == "verify" and ids[0] in active:
            raise RuntimeError("one source graph missing")
        await original_verify(*args, **kwargs)

    @asynccontextmanager
    async def transactional():
        snapshot = copy.deepcopy(client.documents)
        try:
            yield
            if failure == "publish" and ids[0] in active:
                raise OSError("one candidate snapshot failed")
        except BaseException:
            client.documents = snapshot
            raise

    monkeypatch.setattr(client, "add", add)
    monkeypatch.setattr(client, "cognify", cognify)
    monkeypatch.setattr(client, "verify_graph_documents", verify)
    monkeypatch.setattr(client, "write_transaction", transactional)
    coordinator.config.max_retries = 3
    coordinator.config.sync_max_documents_per_group = 8 if bounded else 64
    for mid in ids:
        entry = await store.get(mid)
        entry.content = f"新事实 {mid}"
        await store.update(entry)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    initial = [ids[:4], ids[4:]] if bounded else [ids]
    assert groups == initial
    for mid in ids:
        mapping = await store.get_cognee_mapping(mid)
        assert (mapping["data_id"] != previous[mid]["data_id"]) is (bounded and mid in ids[4:])
    retry_ns = time.time_ns() + 10_000_000_000
    monkeypatch.setattr("agent.memory.store.cognee_queue.time.time_ns", lambda: retry_ns)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    assert groups == initial + ([ids[:2], ids[2:4]] if bounded else [ids[:4], ids[4:]])
    assert coordinator.writer.last_error  # 后一个成功小组不能掩盖前一个小组失败。
    for mid in ids:
        current = await store.get_cognee_mapping(mid)
        assert (current["data_id"] != previous[mid]["data_id"]) is (mid in ids[2 if bounded else 4:])
    remaining = ids[:2 if bounded else 4]
    assert (await store.get_cognee_sync_status())["pending"] == len(remaining)
    await store.close()
    reopened = MemoryStore(store._db_path)
    reopened.set_cognee_projection_enabled(True)
    coordinator = CogneeCoordinator(reopened, client, coordinator.config)
    try:
        retry_ns += 10_000_000_000
        batch = await reopened.claim_cognee_sync_batch(20)
        assert [item["entry_id"] for item in batch] == remaining
        assert all(item["attempts"] == 2 for item in batch)
        await coordinator._process_batch(batch)
        assert groups[-len(remaining):] == [[mid] for mid in remaining]
        status = await reopened.get_cognee_sync_status()
        assert status["pending"] == 0 and status["failed"] == 1
        for mid in ids:
            current = await reopened.get_cognee_mapping(mid)
            assert (current["data_id"] != previous[mid]["data_id"]) is (mid != ids[0])
        assert len(client.documents["anelf_global"]) == 8
        assert "旧事实 0" in client.documents["anelf_global"][previous[ids[0]]["data_id"]]["text"]
        assert all([await reopened.get(mid) is not None for mid in ids])
    finally:
        await reopened.close()


@pytest.mark.parametrize("bounded", [False, True])
async def test_retry_groups_keep_all_fragments_of_each_source_together(coordinator, store, monkeypatch, bounded):
    from agent.memory.graph import fragments

    monkeypatch.setattr(fragments, "MAX_DOCUMENT_CHARS", 400)
    coordinator.config.sync_max_documents_per_group = 1 if bounded else 64
    for i in range(3):
        await store.graph.add_relation(f"topic:左{i}", "连接", f"topic:右{i}", evidence="完整关系证据。" * 100)
    client = coordinator.client
    original_add = client.add
    groups: list[dict[int, int]] = []
    expected: dict[int, int] = {}
    batch = await store.claim_cognee_sync_batch(20)
    for item in batch:
        projection = await store.graph.projection_fragments_snapshot(item["entry_id"], fragmented=False)
        expected[item["entry_id"]] = len(projection[1])
    assert len(expected) == 6 and min(expected.values()) > 1
    blocked = next(iter(expected))

    async def add(data, **kwargs):
        counts: dict[int, int] = {}
        for row in data:
            source = int(row["external_metadata"]["anelf_graph_node_id"])
            counts[source] = counts.get(source, 0) + 1
        assert all(count == expected[source] for source, count in counts.items())
        groups.append(counts)
        return await original_add(data, **kwargs)

    async def cognify(**kwargs):
        if blocked in groups[-1]:
            raise TimeoutError("one relation source unavailable")

    @asynccontextmanager
    async def transactional():
        snapshot = copy.deepcopy(client.documents)
        try:
            yield
        except BaseException:
            client.documents = snapshot
            raise

    monkeypatch.setattr(client, "add", add)
    monkeypatch.setattr(client, "cognify", cognify)
    monkeypatch.setattr(client, "write_transaction", transactional)
    await coordinator._process_batch(batch)
    retry_ns = time.time_ns() + 10_000_000_000
    monkeypatch.setattr("agent.memory.store.cognee_queue.time.time_ns", lambda: retry_ns)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    assert [len(group) for group in groups] == ([1] * 7 if bounded else [6, 3, 3])
    assert coordinator.writer.last_error
    mappings = await store.projection_state.mappings()
    assert len(mappings) == (5 if bounded else 3)
    for mapping in mappings:
        assert len(json.loads(mapping["documents_json"])) == expected[mapping["entry_id"]]
    assert len(client.documents[coordinator.writer.relations_dataset]) == sum(
        expected[mapping["entry_id"]] for mapping in mappings)


@pytest.mark.parametrize("document_limit,character_limit", [(4, 10000), (100, 120)])
def test_publication_budget_counts_only_changed_parts_and_retirement(document_limit, character_limit):
    from agent.memory.cognee.documents import ProjectionDocument
    from agent.memory.cognee.writer import _publication_groups, _source_cost

    item = {"queue_id": 1, "payload": {}, "attempts": 0}
    retained = [(item, ProjectionDocument("old" * 100, f"hash-{i}", {}, part_key=f"part-{i}")) for i in range(10)]
    mapping = {"documents_json": json.dumps([
        {"part_key": f"part-{i}", "content_hash": f"hash-{i}", "data_id": f"id-{i}"} for i in range(11)])}
    retained[-1] = (item, ProjectionDocument("x" * 100, "changed", {}, part_key="part-9"))
    second = ({"queue_id": 2, "payload": {}, "attempts": 0}, ProjectionDocument("y" * 20, "new", {}))
    third = ({"queue_id": 3, "payload": {}, "attempts": 0}, ProjectionDocument("z", "newer", {}))
    assert _source_cost(retained, mapping) == (3, 100)  # 一个变更、两个退役，九篇保留。
    assert _source_cost(retained, mapping, {"id-0"}) == (4, 400)
    config = CogneeConfig(sync_max_documents_per_group=document_limit,
                          sync_max_characters_per_group=character_limit)
    groups = list(_publication_groups([*retained, second, third], {1: mapping, 2: None, 3: None}, config))
    assert [[entry[0]["queue_id"] for entry in group] for group in groups] == [[1] * 10 + [2], [3]]


@pytest.mark.parametrize("operation", ["upsert", "delete"])
async def test_invalid_publication_mapping_fails_claims_without_bypassing_integrity_gate(coordinator, store, monkeypatch, operation):
    from agent.memory.cognee.manifest import DatasetStamp, DocumentStamp, IndexManifest

    ids = [await store.add(MemoryEntry(content=f"原始事实 {i}", memory_type=MemoryType.SEMANTIC)) for i in range(3)]
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    previous = {mid: await store.get_cognee_mapping(mid) for mid in ids}
    first = previous[ids[0]]
    baseline = IndexManifest(datasets={first["dataset_id"]: DatasetStamp(name=first["dataset_name"], documents={
        row["data_id"]: DocumentStamp(identity="original", content_hash=row["content_hash"])
        for row in previous.values()})})

    @asynccontextmanager
    async def transactional():
        baseline.check_sources(await store.projection_state.publication_sources())
        yield

    monkeypatch.setattr(coordinator.client, "write_transaction", transactional)
    add = AsyncMock(wraps=coordinator.client.add)
    monkeypatch.setattr(coordinator.client, "add", add)
    delete = AsyncMock(wraps=coordinator.client.delete_data)
    monkeypatch.setattr(coordinator.client, "delete_data", delete)
    db = await store._conn.get_db()
    async with store._conn.tx(db):
        await db.execute("UPDATE cognee_entry_map SET documents_json='not-json' WHERE entry_id=?", (ids[1],))
    for mid in ids:
        if operation == "delete":
            assert await store.archive_memory(mid)
        else:
            entry = await store.get(mid)
            entry.content = f"更新事实 {mid}"
            await store.update(entry)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    status = await store.get_cognee_sync_status()
    assert status["pending"] == 0 and status["failed"] == 3
    assert coordinator.writer.last_error
    for mid in ids:
        mapping = await store.get_cognee_mapping(mid)
        assert mapping["data_id"] == previous[mid]["data_id"]
        if operation == "upsert":
            assert (await store.get(mid)).content == f"更新事实 {mid}"
    if operation == "delete":
        assert await store.projection_state.staged() == []
        assert {row["id"]: row["content"] for row in await store.list_archived()} == {
            mid: f"原始事实 {i}" for i, mid in enumerate(ids)}
    assert len(coordinator.client.documents[coordinator.writer.memory_dataset]) == 3
    add.assert_not_awaited()
    delete.assert_not_awaited()


@pytest.mark.parametrize("failure", ["none", "delete", "publish", "confirm", "cancel"])
async def test_delete_groups_share_publication_and_resume_after_unconfirmed_commit(coordinator, store, monkeypatch, failure):
    from agent.memory.cognee.manifest import DatasetStamp, DocumentStamp, IndexManifest

    ids = [await store.add(MemoryEntry(content=f"保留原始正文 {i}", memory_type=MemoryType.SEMANTIC)) for i in range(3)]
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    mappings = {mid: await store.get_cognee_mapping(mid) for mid in ids}
    client = coordinator.client
    coordinator.config.sync_max_documents_per_group = 64 if failure == "none" else 2
    transactions = 0
    fail_once = True
    original_delete, original_complete = client.delete_data, store.complete_cognee_sync
    active_store = store

    @asynccontextmanager
    async def transactional():
        nonlocal transactions, fail_once
        snapshot = copy.deepcopy(client.documents)
        baseline = IndexManifest(datasets={name: DatasetStamp(name=name, documents={
            key: DocumentStamp(identity="original", content_hash=value["external_metadata"]["anelf_content_hash"])
            for key, value in documents.items()}) for name, documents in snapshot.items()})
        baseline.check_sources(await active_store.projection_state.publication_sources())
        try:
            yield
            if failure == "publish" and fail_once:
                fail_once = False
                raise OSError("candidate publication failed")
            transactions += 1
        except BaseException:
            client.documents = snapshot
            raise

    async def delete(dataset, document, **kwargs):
        nonlocal fail_once
        await original_delete(dataset, document, **kwargs)
        if fail_once and failure in {"delete", "cancel"}:
            fail_once = False
            if failure == "cancel":
                raise asyncio.CancelledError()
            raise RuntimeError("delete failed after mutation")

    async def complete(*args, **kwargs):
        nonlocal fail_once
        if kwargs.get("delete_mapping") and failure == "confirm" and fail_once:
            fail_once = False
            raise asyncio.CancelledError()
        await original_complete(*args, **kwargs)

    monkeypatch.setattr(client, "write_transaction", transactional)
    monkeypatch.setattr(client, "delete_data", delete)
    monkeypatch.setattr(store, "complete_cognee_sync", complete)
    for mid in ids:
        assert await store.archive_memory(mid)
    batch = await store.claim_cognee_sync_batch(10)
    if failure in {"confirm", "cancel"}:
        with pytest.raises(asyncio.CancelledError):
            await coordinator._process_batch(batch)
        assert len(await store.projection_state.staged()) == 2
        if failure == "confirm":
            assert transactions == 1
            assert not {mappings[mid]["data_id"] for mid in ids[:2]} & set(client.documents[coordinator.writer.memory_dataset])
    else:
        await coordinator._process_batch(batch)
        assert transactions == 1
        if failure == "none":
            assert await store.projection_state.mappings() == []
        else:
            assert {row["entry_id"] for row in await store.projection_state.mappings()} == set(ids[:2])
            assert set(client.documents[coordinator.writer.memory_dataset]) == {mappings[mid]["data_id"] for mid in ids[:2]}
    await store.close()
    reopened = MemoryStore(store._db_path)
    reopened.set_cognee_projection_enabled(True)
    active_store = reopened
    try:
        monkeypatch.setattr(client, "delete_data", original_delete)
        retry_ns = time.time_ns() + 30_000_000_000
        monkeypatch.setattr("agent.memory.store.cognee_queue.time.time_ns", lambda: retry_ns)
        await reopened.requeue_stale_cognee_sync(0)
        coordinator = CogneeCoordinator(reopened, client, coordinator.config)
        await coordinator._process_batch(await reopened.claim_cognee_sync_batch(10))
        assert await reopened.projection_state.mappings() == []
        assert await reopened.projection_state.staged() == []
        status = await reopened.get_cognee_sync_status()
        assert status["pending"] == status["failed"] == 0
        archived = await reopened.list_archived()
        assert {entry["id"]: entry["content"] for entry in archived} == {mid: f"保留原始正文 {i}" for i, mid in enumerate(ids)}
    finally:
        await reopened.close()


async def test_grouped_delete_preserves_new_restore_queued_during_sdk_work(coordinator, store, monkeypatch):
    mid = await store.add(MemoryEntry(content="删除期间恢复的完整正文", memory_type=MemoryType.SEMANTIC, tags=["topic:keep"]))
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    await store.archive_memory(mid)
    original = coordinator.client.delete_data

    async def delete(*args, **kwargs):
        await original(*args, **kwargs)
        assert await store.restore_memory(mid)

    monkeypatch.setattr(coordinator.client, "delete_data", delete)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    assert (await store.get_cognee_sync_status())["pending"] == 1
    assert await store.get_cognee_mapping(mid) is None
    monkeypatch.setattr(coordinator.client, "delete_data", original)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    mapping = await store.get_cognee_mapping(mid)
    entry = await store.get(mid)
    assert mapping and entry.content == "删除期间恢复的完整正文" and entry.tags == ["topic:keep"]
    assert (await store.get_cognee_sync_status())["pending"] == 0


async def test_fragmented_node_restore_replays_all_unconfirmed_retired_parts(coordinator, store, monkeypatch):
    from agent.memory.graph import fragments
    from agent.memory.projection_documents import mapped_documents

    monkeypatch.setattr(fragments, "MAX_DOCUMENT_CHARS", 400)
    edges: list[int] = []
    for i in range(2):
        edge = await store.graph.add_relation("topic:recoverable", "连接", f"topic:peer{i}", evidence="必须完整保留的关系证据。" * 100)
        edges.append(edge["id"])
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    node_id = (await store.graph.get_node("topic:recoverable"))["id"]
    before = await store.get_cognee_mapping(node_id, entry_kind="graph_node")
    parts = mapped_documents(before)
    assert len(parts) > 1

    async def interrupted(*args, **kwargs):
        assert kwargs.get("delete_mapping")
        raise asyncio.CancelledError()

    await store.graph.set_node_archived("topic:recoverable", True)
    with monkeypatch.context() as patch:
        patch.setattr(store, "complete_cognee_sync", interrupted)
        with pytest.raises(asyncio.CancelledError):
            await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    staged = await store.projection_state.staged("graph_node", node_id)
    assert {row["data_id"] for row in staged} == {part.data_id for part in parts}
    assert not {part.data_id for part in parts} & set(coordinator.client.documents[before["dataset_id"]])
    await store.graph.set_node_archived("topic:recoverable", False)
    for edge_id in edges:
        assert await store.graph.set_relation_archived(edge_id, False)
    await store.requeue_stale_cognee_sync(0)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    assert not coordinator.writer.last_error, coordinator.writer.last_error
    after = await store.get_cognee_mapping(node_id, entry_kind="graph_node")
    assert {part.data_id for part in mapped_documents(after)} == {part.data_id for part in parts}
    assert {part.data_id for part in parts} <= set(coordinator.client.documents[after["dataset_id"]])
    assert await store.projection_state.staged() == []
    assert not (await store.projection_state.audit())["missing_or_stale"]


async def test_post_publish_enhancement_failure_does_not_retry_completed_sources(coordinator, store):
    ids = [await store.add(MemoryEntry(content=f"已发布事实 {i}", memory_type=MemoryType.SEMANTIC)) for i in range(4)]
    batch = await store.claim_cognee_sync_batch(20)
    # 模拟重启前已经失败的队列；正常任务不为单条发布增加快照成本。
    for item in batch:
        item["attempts"] = 1
    callback = AsyncMock(side_effect=RuntimeError("optional enhancement unavailable"))
    coordinator.writer._after_write = callback
    await coordinator._process_batch(batch)
    callback.assert_awaited_once_with("anelf_global")
    assert await store.get_cognee_sync_status() == {"pending": 0, "failed": 0, "synced": 4}
    assert all([await store.get_cognee_mapping(mid) is not None for mid in ids])
    assert not coordinator.writer.last_error


async def test_split_retry_cancellation_keeps_commits_and_concurrent_source_update(coordinator, store, monkeypatch):
    ids = [await store.add(MemoryEntry(content=f"断点事实 {i}", memory_type=MemoryType.SEMANTIC)) for i in range(4)]
    client = coordinator.client
    original_add = client.add
    groups: list[list[int]] = []
    entered = asyncio.Event()

    async def add(data, **kwargs):
        groups.append([int(row["external_metadata"]["anelf_memory_id"]) for row in data])
        return await original_add(data, **kwargs)

    async def cognify(**kwargs):
        if len(groups) == 1:
            raise TimeoutError("initial batch timeout")
        if groups[-1] == ids[2:]:
            entered.set()
            await asyncio.Event().wait()

    @asynccontextmanager
    async def transactional():
        snapshot = copy.deepcopy(client.documents)
        try:
            yield
        except BaseException:
            client.documents = snapshot
            raise

    monkeypatch.setattr(client, "add", add)
    monkeypatch.setattr(client, "cognify", cognify)
    monkeypatch.setattr(client, "write_transaction", transactional)
    await coordinator._process_batch(await store.claim_cognee_sync_batch(20))
    fresh = await store.add(MemoryEntry(content="新任务独立发布", memory_type=MemoryType.SEMANTIC))
    retry_ns = time.time_ns() + 10_000_000_000
    monkeypatch.setattr("agent.memory.store.cognee_queue.time.time_ns", lambda: retry_ns)
    task = asyncio.create_task(coordinator._process_batch(await store.claim_cognee_sync_batch(20)))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        entry = await store.get(ids[0])
        entry.content = "小组发布之后更新的事实"
        await store.update(entry)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert groups == [ids, ids[:2], ids[2:]]
    assert (await coordinator.status()).active is None
    assert all([await store.get_cognee_mapping(mid) is not None for mid in ids[:2]])
    assert all([await store.get_cognee_mapping(mid) is None for mid in [*ids[2:], fresh]])
    await store.close()
    reopened = MemoryStore(store._db_path)
    reopened.set_cognee_projection_enabled(True)
    restarted = CogneeCoordinator(reopened, client, coordinator.config)
    monkeypatch.setattr(client, "cognify", AsyncMock())
    try:
        batch = await reopened.claim_cognee_sync_batch(20)
        assert {item["entry_id"] for item in batch} == {ids[0], *ids[2:], fresh}
        assert [item["attempts"] for item in batch if item["entry_id"] in ids[2:]] == [1, 1]
        await restarted._process_batch(batch)
        # 已提交且未变化的来源不再处理；新任务也不与失败来源重新合成同一事务。
        assert groups[-3:] == [[ids[2]], [ids[3]], [fresh, ids[0]]]
        assert await reopened.get_cognee_sync_status() == {"pending": 0, "failed": 0, "synced": 5}
        mapping = await reopened.get_cognee_mapping(ids[0])
        assert "小组发布之后更新的事实" in client.documents["anelf_global"][mapping["data_id"]]["text"]
        assert len(client.documents["anelf_global"]) == 5
        assert await reopened.projection_state.staged() == []
    finally:
        await reopened.close()


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


@pytest.mark.parametrize("block", ["none", "last_old", "empty_old", "referenced", "staged", "unknown", "backup"])
async def test_generation_cleanup_during_build_preserves_sources_progress_and_retained_round(coordinator, store, monkeypatch, block):
    mid = await store.add(MemoryEntry(content="权威正文", memory_type=MemoryType.SEMANTIC))
    legacy_document(coordinator, mid, legacy_memory_text(mid, "旧轮第一份正文"))
    second = legacy_document(coordinator, mid, legacy_memory_text(mid, "旧轮第二份正文"))
    coordinator.client.documents["anelf_legacy"].pop(second["id"])
    coordinator.client.documents["anelf_relations"] = {second["id"]: second}
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    state = coordinator.rebuild.state
    current_name = state.namespace + "_global"
    current = copy.deepcopy(coordinator.client.documents[current_name])
    if block != "last_old":
        state.previous_namespaces.append("anelf_retained")
        coordinator.client.documents["anelf_retained_global"] = {} if block == "empty_old" else copy.deepcopy(current)
    state.phase = "building"
    await store.projection_state.save(state)
    await store.add(MemoryEntry(content="正在等待本轮同步的新正文", memory_type=MemoryType.SEMANTIC))
    coordinator.config.sync_enabled = False
    if block == "referenced":
        db = await store._get_db()
        await db.execute("UPDATE cognee_entry_map SET dataset_id='anelf_legacy'")
        await db.commit()
    elif block == "staged":
        await store.projection_state.stage([("memory", mid, "anelf_legacy", "uncommitted")])
    elif block == "unknown":
        coordinator.client.documents["anelf_relations"]["unknown"] = {"id": "unknown"}
    elif block == "backup":
        monkeypatch.setattr(store.projection_state, "backup", AsyncMock(side_effect=OSError("disk full")))
    before_queue = await store.get_cognee_sync_status()
    before_mappings = await store.projection_state.mappings()
    before_state = state.model_dump()
    delete = coordinator.client.delete_dataset
    calls = []

    async def checked_delete(dataset_id):
        calls.append(dataset_id)
        # 整轮所有成员必须在首个删除前完整归档到已校验备份。
        with sqlite3.connect(coordinator.retirement.status.backup_path) as backup:
            assert backup.execute("SELECT COUNT(*) FROM cognee_legacy_documents").fetchone()[0] == 2
        await delete(dataset_id)

    monkeypatch.setattr(coordinator.client, "delete_dataset", checked_delete)
    for forbidden in (state.namespace, "external"):
        with pytest.raises(ValueError, match="只能清理"):
            await coordinator.retirement.request_generation(forbidden)
    await coordinator.retirement.request_generation("anelf")
    await coordinator.retirement._task
    result = coordinator.retirement.status
    assert coordinator.client.documents[current_name] == current
    assert await store.get_cognee_sync_status() == before_queue
    assert await store.projection_state.mappings() == before_mappings
    assert (await store.get(mid)).content == "权威正文"
    if block == "none":
        assert result.stage == "completed", result.error
        assert result.datasets == result.deleted_datasets == 2
        assert result.documents == result.preserved == 2
        assert set(calls) == {"anelf_legacy", "anelf_relations"}
        assert coordinator.client.documents["anelf_retained_global"] == current
        expected = {**before_state, "previous_namespaces": ["anelf_retained"]}
        assert (await store.projection_state.load()).model_dump() == expected
    else:
        assert result.stage == "failed" and not calls
        assert "anelf_legacy" in coordinator.client.documents and "anelf_relations" in coordinator.client.documents
        assert (await store.projection_state.load()).model_dump() == before_state
        if block in {"last_old", "empty_old"}:
            assert "至少保留一轮" in result.error


async def test_generation_cleanup_rechecks_generation_after_waiting_for_lock(coordinator, store):
    mid = await store.add(MemoryEntry(content="保留的原文", memory_type=MemoryType.SEMANTIC))
    legacy_document(coordinator, mid, legacy_memory_text(mid, "旧原文"))
    await coordinator.rebuild.request(coordinator.config)
    await settle(coordinator)
    coordinator.config.sync_enabled = False
    async with coordinator._projection_lock:
        await coordinator.retirement.request_generation("anelf")
        coordinator.rebuild.state.namespace = "different_current_generation"
    await coordinator.retirement._task
    assert "代际已变化" in coordinator.retirement.status.error
    assert "anelf_legacy" in coordinator.client.documents
