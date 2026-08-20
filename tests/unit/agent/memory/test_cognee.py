"""Cognee 集成单元测试：客户端边界 / 同步 outbox / 协调器投影 / RRF 融合 / 图谱 HTML 净化。"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agent.memory.cognee.client import CogneeClient
from agent.memory.cognee.config import CogneeConfig
from agent.memory.cognee.coordinator import CogneeCoordinator
from agent.memory.cognee.fusion import datasets_for_scope, reciprocal_rank_fusion
from agent.memory.cognee.graph_html import sanitize_cognee_graph_html
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemorySearchResult, MemoryType

# ==================================================================
# CogneeClient 公共边界
# ==================================================================

class _FakeConfig:
    def system_root_directory(self, _path: str) -> None:
        return None

    def data_root_directory(self, _path: str) -> None:
        return None

    def set_llm_config(self, _config: dict) -> None:
        return None

    def set_embedding_config(self, _config: dict) -> None:
        return None


def _configure_without_models(client: CogneeClient):
    async def configure(module) -> None:
        client._module = module
        client._configured = True

    return configure


@pytest.mark.asyncio
async def test_client_normalizes_public_recall(monkeypatch, tmp_path) -> None:
    async def recall(_query: str, **_kwargs):
        return [{
            "source": "graph",
            "text": "graph answer",
            "score": 0.9,
            "dataset_name": "anelf_global",
            "metadata": {"chunk_id": "chunk-1"},
        }]

    fake = SimpleNamespace(
        __version__="1.3.0",
        config=_FakeConfig(),
        recall=recall,
        SearchType=SimpleNamespace(CHUNKS="CHUNKS"),
    )
    client = CogneeClient(CogneeConfig(
        enabled=True,
        data_root=str(tmp_path),
    ))
    monkeypatch.setattr(
        CogneeClient,
        "installed",
        property(lambda _self: True),
    )
    monkeypatch.setattr(client, "_import_cognee", lambda: fake)
    monkeypatch.setattr(client, "_configure", _configure_without_models(client))

    availability = await client.initialize()
    results = await client.recall("query")

    assert availability.ready
    assert availability.version == "1.3.0"
    assert results[0].id == "cognee:chunk-1"
    assert results[0].content == "graph answer"


def test_wal_recovery_patch_defaults_to_tolerant(monkeypatch) -> None:
    """WAL 容错补丁：默认注入 throw_on_wal_replay_failure=False 且幂等。"""
    import sys
    from types import ModuleType

    from agent.memory.cognee.client import _patch_ladybug_wal_recovery

    class _FakeDatabase:
        def __init__(self, database_path=None, **kwargs) -> None:
            self.database_path = database_path
            self.throw_on_wal_replay_failure = kwargs.get(
                "throw_on_wal_replay_failure", True,
            )

    fake_adapter = ModuleType("cognee.infrastructure.databases.graph.ladybug.adapter")
    fake_adapter.Database = _FakeDatabase  # type: ignore[attr-defined]
    fake_package = ModuleType("cognee.infrastructure.databases.graph.ladybug")
    fake_package.adapter = fake_adapter  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules, "cognee.infrastructure.databases.graph.ladybug", fake_package,
    )
    monkeypatch.setitem(
        sys.modules, "cognee.infrastructure.databases.graph.ladybug.adapter",
        fake_adapter,
    )

    _patch_ladybug_wal_recovery()
    patched = fake_adapter.Database  # type: ignore[attr-defined]

    assert patched is not _FakeDatabase
    assert getattr(patched, "_anel_wal_tolerant", False)
    assert issubclass(patched, _FakeDatabase)
    assert patched("/tmp/x.lbug").throw_on_wal_replay_failure is False
    explicit = patched("/tmp/x.lbug", throw_on_wal_replay_failure=True)
    assert explicit.throw_on_wal_replay_failure is True

    # 幂等：重复调用不再二次包装
    _patch_ladybug_wal_recovery()
    assert fake_adapter.Database is patched  # type: ignore[attr-defined]


def test_client_exposes_documented_public_boundary() -> None:
    expected = {
        "remember", "recall", "improve", "forget", "serve", "disconnect",
        "push", "export", "add", "cognify", "search", "memify", "update",
        "run_custom_pipeline", "run_migrations", "list_datasets",
        "discover_datasets", "list_data", "has_data", "get_dataset_status",
        "empty_dataset", "delete_data", "delete_all", "prune_data",
        "prune_system", "visualize", "visualize_graph",
        "get_schema_inventory", "get_memory_provenance_graph",
        "visualize_memory_provenance", "enable_tracing", "disable_tracing",
        "get_last_trace", "get_all_traces", "clear_traces",
    }
    assert expected.issubset(set(dir(CogneeClient)))


@pytest.mark.asyncio
async def test_disabled_client_never_imports_optional_package(monkeypatch) -> None:
    client = CogneeClient(CogneeConfig(enabled=False))

    def fail_import():
        raise AssertionError("disabled client must remain lazy")

    monkeypatch.setattr(client, "_import_cognee", fail_import)
    availability = await client.initialize()

    assert not availability.ready
    assert not availability.enabled


def test_config_resolves_absolute_windows_storage_path(tmp_path) -> None:
    config = CogneeConfig(data_root=str(tmp_path))
    assert config.absolute_data_root == str(tmp_path.resolve())


# ==================================================================
# 同步 outbox（MemoryStore cognee 投影队列）
# ==================================================================

@pytest.mark.asyncio
async def test_memory_write_and_projection_are_persisted_together(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="remember this",
            tags=["user:42"],
        ))

        entry = await store.get(memory_id)
        batch = await store.claim_cognee_sync_batch(10)

        assert entry is not None
        assert len(batch) == 1
        assert batch[0]["entry_kind"] == "memory"
        assert batch[0]["entry_id"] == memory_id
        assert batch[0]["operation"] == "upsert"
        assert batch[0]["payload"]["content"] == "remember this"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_projection_failure_can_be_retried(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="retry me",
        ))
        item = (await store.claim_cognee_sync_batch(1))[0]

        await store.fail_cognee_sync(
            item["queue_id"],
            "temporary",
            max_retries=1,
            retry_delay_seconds=1,
        )
        status = await store.get_cognee_sync_status()
        retried = await store.retry_failed_cognee_sync()

        assert memory_id > 0
        assert status["failed"] == 1
        assert retried == 1
        assert (await store.get_cognee_sync_status())["pending"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_delete_queues_projection_cleanup(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="delete me",
        ))
        first = (await store.claim_cognee_sync_batch(1))[0]
        await store.complete_cognee_sync(
            first["queue_id"],
            memory_id,
            dataset_name="anelf_global",
            dataset_id="dataset-id",
            data_id="data-id",
        )

        assert await store.delete(memory_id)
        deletion = (await store.claim_cognee_sync_batch(1))[0]

        assert deletion["operation"] == "delete"
        mapping = await store.get_cognee_mapping(memory_id)
        assert mapping is not None
        assert mapping["data_id"] == "data-id"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_reset_cognee_projection_clears_queue_and_mappings(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="to be reset",
        ))
        item = (await store.claim_cognee_sync_batch(1))[0]
        await store.complete_cognee_sync(
            item["queue_id"],
            memory_id,
            dataset_name="anelf_global",
            dataset_id="dataset-id",
            data_id="data-id",
        )
        # 一条已同步映射 + 一条待处理队列（delete 操作）
        await store.delete(memory_id)
        before = await store.get_cognee_sync_status()
        assert before["synced"] == 1
        assert before["pending"] == 1

        cleared = await store.reset_cognee_projection()

        assert cleared == {"queue": 1, "mappings": 1}
        after = await store.get_cognee_sync_status()
        assert after == {"pending": 0, "failed": 0, "synced": 0}
        assert await store.get_cognee_mapping(memory_id) is None
    finally:
        await store.close()


# ==================================================================
# CogneeCoordinator 投影与删除
# ==================================================================

class _FakeCogneeClient:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.deleted: list[tuple[str, str]] = []

    async def initialize(self):
        return SimpleNamespace(ready=True, reason="")

    async def make_data_item(self, data: str, *, label: str, external_metadata: dict):
        return {"data": data, "label": label, "external_metadata": external_metadata}

    async def add(self, data, **_kwargs):
        self.items.extend(data)

    async def cognify(self, **_kwargs):
        return None

    async def improve(self, **_kwargs):
        return None

    async def list_datasets(self):
        return [SimpleNamespace(id="dataset-id", name="anelf_global")]

    async def list_data(self, _dataset_id):
        return [
            SimpleNamespace(
                id=f"data-{item['external_metadata']['anelf_memory_id']}",
                external_metadata=item["external_metadata"],
            )
            for item in self.items
        ]

    async def delete_data(self, dataset_id, data_id, **_kwargs):
        self.deleted.append((str(dataset_id), str(data_id)))


@pytest.mark.asyncio
async def test_coordinator_projects_and_deletes_memory(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = _FakeCogneeClient()
    coordinator = CogneeCoordinator(
        store,
        client,
        CogneeConfig(enabled=True, sync_enabled=True),
    )
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="projected",
        ))
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))

        mapping = await store.get_cognee_mapping(memory_id)
        assert mapping is not None
        assert mapping["dataset_id"] == "dataset-id"
        assert mapping["data_id"] == f"data-{memory_id}"

        await store.delete(memory_id)
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))

        assert client.deleted[-1] == ("dataset-id", f"data-{memory_id}")
        assert await store.get_cognee_mapping(memory_id) is None
    finally:
        await store.close()


class _GraphFakeClient(_FakeCogneeClient):
    """relations 数据集假客户端（按 anelf_graph_node_id 反解数据 ID）。"""

    async def list_datasets(self):
        return [SimpleNamespace(id="rel-dataset-id", name="anelf_relations")]

    async def list_data(self, _dataset_id):
        return [
            SimpleNamespace(
                id=f"data-{item['external_metadata']['anelf_graph_node_id']}",
                external_metadata=item["external_metadata"],
            )
            for item in self.items
        ]


@pytest.mark.asyncio
async def test_coordinator_projects_graph_nodes(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = _GraphFakeClient()
    coordinator = CogneeCoordinator(
        store,
        client,
        CogneeConfig(enabled=True, sync_enabled=True),
    )
    try:
        edge = await store.graph.add_relation(
            "user:qq:1", "朋友", "user:qq:2",
            subject_label="阿辰", object_label="老王", evidence="常一起吃饭",
        )
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))

        # 两端节点均投影到 relations 数据集，文档含关系内容
        for node_id in (edge["subject"]["id"], edge["object"]["id"]):
            mapping = await store.get_cognee_mapping(node_id, entry_kind="graph_node")
            assert mapping is not None
            assert mapping["dataset_name"] == "anelf_relations"
            assert mapping["data_id"] == f"data-{node_id}"
        assert any("朋友" in item["data"] and "老王" in item["data"] for item in client.items)

        # 节点归档 → 删除其投影
        await store.graph.set_node_archived("user:qq:2", True)
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        assert ("rel-dataset-id", f"data-{edge['object']['id']}") in client.deleted
        assert await store.get_cognee_mapping(
            edge["object"]["id"], entry_kind="graph_node",
        ) is None
    finally:
        await store.close()


# ==================================================================
# RRF 融合与数据集作用域
# ==================================================================

def test_datasets_for_scope_isolated_and_hashed() -> None:
    config = CogneeConfig(dataset_prefix="test")

    first = datasets_for_scope(config, "user_sensitive-id", None)
    second = datasets_for_scope(config, "user_other-id", None)

    assert first[0] == "test_global"
    assert first[1] == "test_relations"  # 关系网络数据集对所有 scope 开放
    assert len(first) == 3
    assert "sensitive-id" not in first[2]
    assert first[2] != second[2]


def test_rrf_deduplicates_projected_native_memory() -> None:
    config = CogneeConfig(native_weight=1.0, cognee_weight=0.8)
    native = [
        MemorySearchResult(
            id="mem:7",
            snippet="The user prefers concise answers.",
            score=0.7,
            source="memory",
        ),
    ]
    projected = [
        MemorySearchResult(
            id="cognee:chunk",
            snippet=(
                "Memory type: semantic\nSource: test\nImportance: 0.7\n"
                "Tags: user:1\nMetadata: {}\n\n"
                "The user prefers concise answers."
            ),
            score=0.95,
            source="cognee_chunk",
        ),
    ]

    results = reciprocal_rank_fusion(native, projected, config=config, limit=5)

    assert len(results) == 1
    assert results[0].source == "memory"
    assert results[0].score == 1.0


# ==================================================================
# 图谱 HTML 净化
# ==================================================================

def test_sanitize_inlines_local_d3_and_strips_google_fonts() -> None:
    raw = """
    <html><head>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    </head><body>ok</body></html>
    """
    out = sanitize_cognee_graph_html(raw)
    assert 'src="https://d3js.org' not in out
    assert "fonts.googleapis.com" not in out
    assert "fonts.gstatic.com" not in out
    assert out.count("<script>") >= 1
    assert "zoomIdentity" in out
    assert "ok" in out


def test_sanitize_rejects_html_without_d3() -> None:
    with pytest.raises(RuntimeError, match="d3"):
        sanitize_cognee_graph_html("<html><body>no graph</body></html>")


# ==================================================================
# LanceDB 物理压缩
# ==================================================================

def test_compact_lance_tree_reclaims_history_versions(tmp_path) -> None:
    """真实 lancedb：多次写入制造历史版本，压缩后仅保留最新版本且数据完好。"""
    lancedb = pytest.importorskip("lancedb")
    from agent.memory.cognee.storage import compact_lance_tree

    db_dir = tmp_path / "system" / "databases" / "user" / "ds.lance.db"
    db_dir.mkdir(parents=True)
    connection = lancedb.connect(str(db_dir))
    table = connection.create_table(
        "DocumentChunk_text",
        [{"id": i, "vector": [float(i)] * 8, "text": f"doc{i}"} for i in range(10)],
    )
    for extra in range(5):
        table.add([{"id": 100 + extra, "vector": [0.0] * 8, "text": f"extra{extra}"}])
    table.delete("id < 5")

    versions_dir = db_dir / "DocumentChunk_text.lance" / "_versions"
    assert len(list(versions_dir.iterdir())) > 2

    result = compact_lance_tree(tmp_path / "system" / "databases", 0.0)

    assert result["databases"] == 1
    assert result["tables"] == 1
    assert result["errors"] == []
    assert result["bytes_reclaimed"] > 0
    # after_stats 为压缩后实测占用，与 bytes_after 同口径（供统计缓存直接收录）
    assert result["after_stats"]["total_bytes"] == result["bytes_after"]
    assert result["after_stats"]["lance_bytes"] > 0
    assert len(list(versions_dir.iterdir())) <= 2
    reopened = lancedb.connect(str(db_dir)).open_table("DocumentChunk_text")
    assert reopened.count_rows() == 10


def test_compact_lance_tree_missing_root_is_noop(tmp_path) -> None:
    pytest.importorskip("lancedb")
    from agent.memory.cognee.storage import compact_lance_tree

    result = compact_lance_tree(tmp_path / "nonexistent", 7.0)

    assert result["databases"] == 0
    assert result["tables"] == 0
    assert result["errors"] == []


def test_storage_stats_classifies_components(tmp_path) -> None:
    """存储统计按 向量/图/元数据/原始文档 正确归类，总口径为整个数据目录。"""
    from agent.memory.cognee.storage import compute_storage_stats

    def put(rel: str, size: int) -> None:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)

    put("data/dataset-1/doc.txt", 10)
    put("system/databases/cognee_db", 100)
    put("system/databases/user/ds.lance.db/T.lance/_versions/1.manifest", 1000)
    put("system/databases/user/ds.lbug", 500)
    put("system/databases/user/ds.lbug.wal", 50)
    put("system/databases/user/misc.lock", 5)
    put("system/logs/cognee.log", 7)

    stats = compute_storage_stats(tmp_path)

    assert stats["data_bytes"] == 10
    assert stats["metadata_bytes"] == 100
    assert stats["lance_bytes"] == 1000
    assert stats["graph_bytes"] == 550
    assert stats["other_bytes"] == 12
    assert stats["total_bytes"] == 1672


@pytest.mark.asyncio
async def test_storage_stats_never_blocks_on_cold_cache(tmp_path) -> None:
    """首次调用立即返回空统计并后台刷新；刷新完成后返回真实值并落快照。"""
    import asyncio

    from agent.memory.cognee.storage import StorageStatsTracker

    tracker = StorageStatsTracker()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "doc.bin").write_bytes(b"x" * 100)

    first = await tracker.get(str(tmp_path))
    assert first["total_bytes"] == 0  # 冷缓存不等待遍历

    for _ in range(100):
        await asyncio.sleep(0.05)
        if (await tracker.get(str(tmp_path)))["total_bytes"] == 100:
            break
    else:
        pytest.fail("后台存储统计刷新未完成")
    assert (tmp_path / "storage_stats.json").exists()


@pytest.mark.asyncio
async def test_storage_stats_snapshot_restores_after_restart(tmp_path) -> None:
    """磁盘快照模拟重启恢复：清内存后立即返回上次真实值而非 0；定向失效连快照一起删。"""
    import asyncio
    import json as jsonlib

    from agent.memory.cognee.storage import StorageStatsTracker

    snapshot = {
        "computed_at": 1.0,
        "stats": {
            "total_bytes": 500, "data_bytes": 100, "lance_bytes": 300,
            "graph_bytes": 50, "metadata_bytes": 40, "other_bytes": 10,
        },
    }
    (tmp_path / "storage_stats.json").write_text(jsonlib.dumps(snapshot))

    tracker = StorageStatsTracker()
    stats = await tracker.get(str(tmp_path))
    assert stats["total_bytes"] == 500  # 来自快照而非重新遍历

    # 后台刷新完成后会用真实遍历值覆盖快照
    for _ in range(100):
        await asyncio.sleep(0.05)
        if (await tracker.get(str(tmp_path)))["total_bytes"] != 500:
            break
    else:
        pytest.fail("后台存储统计刷新未完成")

    tracker.invalidate(str(tmp_path))
    assert not (tmp_path / "storage_stats.json").exists()
    assert (await tracker.get(str(tmp_path)))["total_bytes"] == 0


@pytest.mark.asyncio
async def test_storage_stats_adopt_wins_over_inflight_refresh(
    tmp_path, monkeypatch,
) -> None:
    """在途遍历期间发生 adopt（压缩实测值）：代际递增使旧遍历结果被丢弃。"""
    import asyncio
    import threading

    from agent.memory.cognee import storage

    gate = threading.Event()

    def slow_compute(_root):
        gate.wait(10)
        return storage.StorageStatsDict(
            total_bytes=100, data_bytes=0, lance_bytes=100,
            graph_bytes=0, metadata_bytes=0, other_bytes=0,
        )

    monkeypatch.setattr(storage, "compute_storage_stats", slow_compute)
    tracker = storage.StorageStatsTracker(ttl_seconds=0.0)
    root = str(tmp_path)

    tracker.schedule_refresh(root)
    for _ in range(100):
        await asyncio.sleep(0.02)
        if root in tracker._refreshing:
            break
    fresh = storage.StorageStatsDict(
        total_bytes=500, data_bytes=0, lance_bytes=500,
        graph_bytes=0, metadata_bytes=0, other_bytes=0,
    )
    tracker.adopt(root, fresh)
    gate.set()
    for _ in range(100):
        await asyncio.sleep(0.02)
        if root not in tracker._refreshing:
            break

    assert tracker._cache[root][1]["total_bytes"] == 500  # 未被在途旧遍历覆盖


class _CompactFakeClient:
    def __init__(self) -> None:
        self.compact_calls: list[float] = []

    async def compact_vector_storage(self, retention_days: float) -> dict:
        self.compact_calls.append(retention_days)
        return {
            "databases": 1,
            "tables": 2,
            "bytes_before": 1000,
            "bytes_after": 400,
            "bytes_reclaimed": 600,
            "errors": [],
            "after_stats": {
                "total_bytes": 400, "data_bytes": 0, "lance_bytes": 400,
                "graph_bytes": 0, "metadata_bytes": 0, "other_bytes": 0,
            },
        }


@pytest.mark.asyncio
async def test_coordinator_compact_idle_scheduling(tmp_path) -> None:
    """空闲窗口压缩：显式请求立即执行，之后受间隔门控；禁用时不执行。"""
    import asyncio

    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    client = _CompactFakeClient()
    coordinator = CogneeCoordinator(
        store,
        client,
        CogneeConfig(
            enabled=True,
            data_root=str(tmp_path / "cognee"),
            compact_interval_seconds=3600.0,
            compact_retention_days=3.0,
        ),
    )
    try:
        coordinator._last_compact_ns = time.monotonic_ns()  # 间隔未到期
        await coordinator._maybe_compact()
        assert client.compact_calls == []

        await coordinator.request_compact()  # worker 未运行 → 内联执行
        assert client.compact_calls == [3.0]
        assert coordinator.last_compact_at > 0
        assert "回收" in coordinator.last_compact_summary

        coordinator._compact_requested = True
        await coordinator._maybe_compact()  # 刚执行过，间隔未到期但被显式请求 → 仍执行
        assert client.compact_calls == [3.0, 3.0]

        coordinator.config.compact_enabled = False
        coordinator._compact_requested = True
        await coordinator._maybe_compact()
        assert client.compact_calls == [3.0, 3.0]
        assert coordinator._compact_requested is False
        await asyncio.sleep(0.05)  # 等 adopt 的快照保存任务收尾
    finally:
        await store.close()
