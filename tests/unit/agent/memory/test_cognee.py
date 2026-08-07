"""Cognee 集成单元测试：客户端边界 / 同步 outbox / 协调器投影 / RRF 融合 / 图谱 HTML 净化。"""

from __future__ import annotations

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
