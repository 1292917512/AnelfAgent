"""用真实 SQLite 验证构建、并发更新、断点恢复和后端验收边界。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock
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
