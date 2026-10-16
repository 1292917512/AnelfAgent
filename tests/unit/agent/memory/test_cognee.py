"""Cognee 集成单元测试：客户端边界 / 同步 outbox / 协调器投影 / RRF 融合 / 定向检索。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent.memory.cognee.client import CogneeClient
from agent.memory.cognee.config import (
    CogneeChatModelConfig,
    CogneeConfig,
    CogneeEmbeddingModelConfig,
    load_cognee_config,
)
from agent.memory.cognee.coordinator import CogneeCoordinator
from agent.memory.cognee.fusion import datasets_for_scope, reciprocal_rank_fusion
from agent.memory.cognee.llm_bridge import CogneeConfigError
from agent.memory.cognee.types import CogneeAvailability
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


def _configure_without_models(
    client: CogneeClient, module: SimpleNamespace,
) -> Callable[[], Awaitable[None]]:
    async def configure() -> None:
        client._module = module
        client._configured = True

    return configure


@pytest.mark.parametrize("expire_total", [False, True])
async def test_client_distinguishes_internal_timeout_from_pipeline_deadline(expire_total):
    cancelled = asyncio.Event()
    source = TimeoutError("provider timed out")

    async def cognify(**_kwargs):
        if not expire_total:
            raise source
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = CogneeClient(CogneeConfig())
    client._configured = True
    client._module = SimpleNamespace(cognify=cognify)
    with pytest.raises(TimeoutError) as caught:
        await client._call("cognify", timeout=0.01 if expire_total else 120)
    if expire_total:
        assert "总时限已到" in str(caught.value)
        assert cancelled.is_set()
    else:
        assert "内部步骤超时" in str(caught.value)
        assert "provider timed out" in str(caught.value)
        assert caught.value.__cause__ is source


async def test_client_preserves_caller_context_and_propagates_cancellation():
    context = ContextVar("cognee_call_test", default="host")
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def cognify(**_kwargs):
        context.set("pipeline")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    client = CogneeClient(CogneeConfig())
    client._configured = True
    client._module = SimpleNamespace(cognify=cognify)

    async def caller():
        try:
            await client.cognify()
        finally:
            assert context.get() == "host"

    task = asyncio.create_task(caller())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


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
    monkeypatch.setattr(client, "_configure", _configure_without_models(client, fake))

    availability = await client.initialize()
    results = await client.recall("query")

    assert availability.ready
    assert availability.version == "1.3.0"
    assert results[0].id == "cognee:chunk-1"
    assert results[0].content == "graph answer"


@pytest.mark.asyncio
async def test_list_datasets_degrades_when_database_not_created(monkeypatch, tmp_path) -> None:
    """重建清场后引擎待重启的中间态：数据集查询降级为空列表而非 500。"""

    class DatabaseNotCreatedError(Exception):
        pass

    async def list_datasets():
        raise DatabaseNotCreatedError("The database has not been created yet.")

    fake = SimpleNamespace(
        __version__="1.3.0",
        config=_FakeConfig(),
        datasets=SimpleNamespace(list_datasets=list_datasets),
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
    monkeypatch.setattr(client, "_configure", _configure_without_models(client, fake))

    await client.initialize()

    assert await client.list_datasets() == []


def test_wal_recovery_patch_defaults_to_tolerant() -> None:
    """WAL 容错补丁：默认注入 throw_on_wal_replay_failure=False 且幂等。"""
    from agent.memory.cognee.ladybug_guard import _patch_wal_recovery

    class _FakeDatabase:
        def __init__(self, database_path=None, **kwargs) -> None:
            self.database_path = database_path
            self.throw_on_wal_replay_failure = kwargs.get(
                "throw_on_wal_replay_failure", True,
            )

    class _FakeAdapterModule:
        Database = _FakeDatabase

    _patch_wal_recovery(_FakeAdapterModule)
    patched = _FakeAdapterModule.Database

    assert patched is not _FakeDatabase
    assert getattr(patched, "_anel_wal_tolerant", False)
    assert issubclass(patched, _FakeDatabase)
    assert patched("/tmp/x.lbug").throw_on_wal_replay_failure is False
    explicit = patched("/tmp/x.lbug", throw_on_wal_replay_failure=True)
    assert explicit.throw_on_wal_replay_failure is True

    # 幂等：重复调用不再二次包装
    _patch_wal_recovery(_FakeAdapterModule)
    assert _FakeAdapterModule.Database is patched


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


@pytest.mark.parametrize("saved", [None, {}, {"enabled": False}, {"enabled": True}])
def test_config_defaults_on_and_preserves_explicit_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, saved: dict[str, bool] | None,
) -> None:
    path = tmp_path / "cognee.json"
    monkeypatch.setattr("core.path.ConfigPaths.COGNEE_CONFIG", str(path))
    if saved is not None:
        path.write_text(json.dumps(saved), encoding="utf-8")

    config = load_cognee_config()

    assert config.enabled is (saved or {}).get("enabled", True)
    assert config.chat.source == config.embedding.source == "auto"


@pytest.mark.asyncio
async def test_client_waits_for_models_then_initializes_without_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path / "cognee")))
    fake = SimpleNamespace(__version__="1.6.2", config=_FakeConfig())

    def import_cognee() -> SimpleNamespace:
        client._module = fake
        return fake

    importer = Mock(side_effect=import_cognee)
    chat = Mock(side_effect=CogneeConfigError("请配置聊天模型"))
    embedding = Mock(return_value=None)
    monkeypatch.setattr(CogneeClient, "installed", property(lambda _self: True))
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: object())
    monkeypatch.setattr("agent.memory.cognee.client.resolve_chat_llm_config", chat)
    monkeypatch.setattr("agent.memory.cognee.client.resolve_embedding_llm_config", embedding)
    monkeypatch.setattr(client, "_import_cognee", importer)

    availability = await client.initialize()
    assert availability.enabled and availability.configuration_required
    assert not availability.ready
    importer.assert_not_called()

    chat.side_effect = None
    chat.return_value = {"llm_provider": "openai", "llm_model": "test-chat"}
    availability = await client.initialize()
    assert availability.configuration_required
    assert "Embedding" in availability.reason
    importer.assert_not_called()
    assert not (tmp_path / "cognee").exists()

    embedding.return_value = {"embedding_provider": "openai", "embedding_model": "test-embed"}
    availability = await client.initialize()
    assert availability.ready and not availability.configuration_required
    assert availability.reason == ""
    importer.assert_called_once()
    assert client.resolved_info["embedding"]["model"] == "test-embed"


@pytest.mark.parametrize("reconfigure", [False, True])
async def test_client_serializes_initialization_and_applies_latest_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reconfigure: bool,
) -> None:
    def config(model: str) -> CogneeConfig:
        return CogneeConfig(
            data_root=str(tmp_path / model),
            chat=CogneeChatModelConfig(source="custom", provider="openai", model=model),
            embedding=CogneeEmbeddingModelConfig(source="custom", model="embedding"),
        )

    client = CogneeClient(config("initial"))
    module = SimpleNamespace(config=Mock())
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    imports = []

    def load() -> SimpleNamespace:
        if client._module is None:
            imports.append(True)
            loop.call_soon_threadsafe(started.set)
            if not release.wait(5):
                raise TimeoutError("test import was not released")
            client._module = module
        return module

    monkeypatch.setattr(CogneeClient, "installed", property(lambda _self: True))
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: object())
    monkeypatch.setattr(client, "_import_cognee", load)
    first = asyncio.create_task(client.initialize())
    second = asyncio.create_task(client.initialize())
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        if reconfigure:
            client.reconfigure(config("updated"))
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
        assert all(result.ready for result in results)
        assert len(imports) == 1
        module.config.set_llm_config.assert_called_once()
        payload = module.config.set_llm_config.call_args.args[0]
        expected = "updated" if reconfigure else "initial"
        assert payload["llm_model"] == expected
        module.config.system_root_directory.assert_called_once_with(str(tmp_path / expected / "system"))
    finally:
        release.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)


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
        self.ready = True
        self.items: list[dict] = []
        self.deleted: list[tuple[str, str]] = []

    async def initialize(self) -> CogneeAvailability:
        return CogneeAvailability(
            installed=True, enabled=True, ready=self.ready,
            configuration_required=not self.ready,
            reason="" if self.ready else "请配置模型",
        )

    async def make_data_item(self, data: str, *, label: str, external_metadata: dict, dataset_name: str = ""):
        entry_id = external_metadata.get("anelf_memory_id") or external_metadata["anelf_graph_node_id"]
        return {"data": data, "label": label, "external_metadata": external_metadata, "data_id": f"data-{entry_id}"}

    async def add(self, data, **kwargs):
        self.items.extend(data)
        dataset = next(row for row in await self.list_datasets() if row.name == kwargs["dataset_name"])
        return SimpleNamespace(dataset_id=dataset.id)

    async def cognify(self, **_kwargs):
        return None

    async def improve(self, **_kwargs):
        return None

    async def list_datasets(self):
        return [SimpleNamespace(id="dataset-id", name="anelf_global")]

    async def available_datasets(self, names):
        return names

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
async def test_start_prepares_dataset_directory_without_running_recall(monkeypatch, tmp_path, store):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path), sync_enabled=False))
    monkeypatch.setattr(client, "initialize", AsyncMock(return_value=CogneeAvailability(
        installed=True, enabled=True, ready=True,
    )))
    datasets = AsyncMock(return_value=[{"name": "anelf_global"}])
    monkeypatch.setattr(client, "list_datasets", datasets)
    recall = AsyncMock()
    monkeypatch.setattr(client, "recall", recall)
    monkeypatch.setattr("agent.memory.cognee.coordinator.cognee_storage_stats.schedule_refresh", Mock())
    coordinator = CogneeCoordinator(store, client, client.config)
    try:
        await coordinator.start()
        assert await client.available_datasets(["anelf_global", "missing"]) == ["anelf_global"]
        datasets.assert_awaited_once()
        recall.assert_not_awaited()
    finally:
        await coordinator.close()


@pytest.mark.asyncio
async def test_worker_preserves_pending_until_models_are_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, store: MemoryStore,
) -> None:
    store.set_cognee_projection_enabled(True)
    memory_id = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content="waiting"))
    client = _FakeCogneeClient()
    client.ready = False
    coordinator = CogneeCoordinator(store, client, CogneeConfig(data_root=str(tmp_path)))
    idle = asyncio.Event()
    projected = asyncio.Event()
    process_batch = coordinator._process_batch

    async def mark_idle() -> None:
        idle.set()

    async def process_and_notify(batch: list[dict[str, Any]]) -> None:
        await process_batch(batch)
        projected.set()

    claim = AsyncMock(wraps=store.claim_cognee_sync_batch)
    monkeypatch.setattr(store, "claim_cognee_sync_batch", claim)
    monkeypatch.setattr(coordinator, "_run_idle_job", mark_idle)
    monkeypatch.setattr(coordinator, "_process_batch", process_and_notify)
    monkeypatch.setattr(coordinator, "_maybe_compact", AsyncMock())
    monkeypatch.setattr("agent.memory.cognee.coordinator.cognee_storage_stats.schedule_refresh", Mock())
    try:
        await coordinator.start()
        await asyncio.wait_for(idle.wait(), timeout=5)
        claim.assert_not_awaited()
        status = await coordinator.status()
        assert status.pending == 1 and status.failed == 0 and status.last_error == ""

        client.ready = True
        coordinator.wake()
        await asyncio.wait_for(projected.wait(), timeout=5)
        mapping = await store.get_cognee_mapping(memory_id)
        assert mapping is not None
        assert mapping["data_id"] == f"data-{memory_id}"
    finally:
        await coordinator.close()


@pytest.mark.asyncio
async def test_coordinator_projects_and_deletes_memory(tmp_path) -> None:
    from agent.memory.provenance import note_origin

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
            tags=["topic:deployment"], source="auto_capture",
            metadata={**note_origin("user_qq:1"), "sensitivity": "private"},
        ))
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))

        attribution = client.items[0]["external_metadata"]["anelf_attribution"]
        assert attribution["tags"] == ["topic:deployment"]
        assert attribution["sensitivity"] == "private"
        assert attribution["provenance"]["origins"][0]["scope"] == "user_qq:1"
        assert f"Memory ID: {memory_id}\n" in client.items[0]["data"]

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
    """图谱投影假客户端（按 anelf_graph_node_id 反解数据 ID；含 per-scope 关系数据集）。"""

    async def list_datasets(self):
        digest_1 = hashlib.sha256(b"qq:1").hexdigest()[:16]
        digest_2 = hashlib.sha256(b"qq:2").hexdigest()[:16]
        return [
            SimpleNamespace(id="rel-dataset-id", name="anelf_relations"),
            SimpleNamespace(id="rel-qq-1", name=f"anelf_relations_user_{digest_1}"),
            SimpleNamespace(id="rel-qq-2", name=f"anelf_relations_user_{digest_2}"),
        ]

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

        # 实体型节点投影到各自 per-scope 关系数据集（scope 隔离），文档含关系内容
        digest_1 = hashlib.sha256(b"qq:1").hexdigest()[:16]
        digest_2 = hashlib.sha256(b"qq:2").hexdigest()[:16]
        expected = {
            edge["subject"]["id"]: f"anelf_relations_user_{digest_1}",
            edge["object"]["id"]: f"anelf_relations_user_{digest_2}",
        }
        for node_id, dataset_name in expected.items():
            mapping = await store.get_cognee_mapping(node_id, entry_kind="graph_node")
            assert mapping is not None
            assert mapping["dataset_name"] == dataset_name
            assert mapping["data_id"] == f"data-{node_id}"
        assert any("朋友" in item["data"] and "老王" in item["data"] for item in client.items)

        # 节点归档 → 删除其投影（按 mapping 指向的数据集路由）
        await store.graph.set_node_archived("user:qq:2", True)
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        assert ("rel-qq-2", f"data-{edge['object']['id']}") in client.deleted
        assert await store.get_cognee_mapping(
            edge["object"]["id"], entry_kind="graph_node",
        ) is None
    finally:
        await store.close()


# ==================================================================
# 内容指纹跳过（防无效变更重跑图谱抽取）
# ==================================================================

@pytest.mark.asyncio
async def test_unchanged_upsert_skips_enqueue_after_sync(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = _FakeCogneeClient()
    coordinator = CogneeCoordinator(
        store, client, CogneeConfig(enabled=True, sync_enabled=True),
    )
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="stable content",
        ))
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        mapping = await store.get_cognee_mapping(memory_id)
        assert mapping is not None
        assert mapping["content_hash"]

        # importance 漂移：内容指纹未变 → 不入队、不重跑管线
        await store.update_importance(memory_id, 0.9)
        assert await store.claim_cognee_sync_batch(10) == []
        assert len(client.items) == 1

        # 真实内容变更仍正常入队与重投影
        entry = await store.get(memory_id)
        assert entry is not None
        entry.content = "changed content"
        await store.update(entry)
        batch = await store.claim_cognee_sync_batch(10)
        assert len(batch) == 1
        await coordinator._process_batch(batch)
        assert len(client.items) == 2
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_backfill_bypasses_fingerprint_skip(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="backfill me",
        ))
        item = (await store.claim_cognee_sync_batch(1))[0]
        await store.complete_cognee_sync(
            item["queue_id"], memory_id,
            dataset_name="anelf_global", dataset_id="dataset-id", data_id="data-id",
            content_hash="fingerprint",
        )
        assert (await store.get_cognee_sync_status())["pending"] == 0

        # 回填是显式修复语义：指纹一致也必须重新入队
        queued = await store.enqueue_cognee_backfill(limit=0)

        assert queued == 1
        assert (await store.get_cognee_sync_status())["pending"] == 1
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_processing_entry_blocks_fingerprint_skip(tmp_path) -> None:
    """在途批次持有更新版本的负载并会覆写映射：内容回退时不得跳过入队。"""
    from agent.memory.store._shared import (
        entry_projection_payload,
        projection_content_hash,
    )

    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = _FakeCogneeClient()
    coordinator = CogneeCoordinator(
        store, client, CogneeConfig(enabled=True, sync_enabled=True),
    )
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="version A",
        ))
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))

        # 变更为 B 并被 worker 认领（processing）
        entry = await store.get(memory_id)
        assert entry is not None
        entry.content = "version B"
        await store.update(entry)
        inflight = await store.claim_cognee_sync_batch(1)
        assert len(inflight) == 1

        # 内容回退到 A：指纹与已同步映射一致，但在途批次未收尾 → 照常入队
        entry.content = "version A"
        await store.update(entry)
        pending = await store.claim_cognee_sync_batch(10)
        assert [item["entry_id"] for item in pending] == [memory_id]

        # 两个批次顺序落地后，映射指纹与当前内容一致
        await coordinator._process_batch(inflight)
        await coordinator._process_batch(pending)
        current = entry_projection_payload(await store.get(memory_id), memory_id)
        mapping = await store.get_cognee_mapping(memory_id)
        assert mapping is not None
        assert mapping["content_hash"] == projection_content_hash(current)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_graph_unchanged_document_skips_reprojection(tmp_path) -> None:
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = _GraphFakeClient()
    coordinator = CogneeCoordinator(
        store, client, CogneeConfig(enabled=True, sync_enabled=True),
    )
    try:
        await store.graph.add_relation(
            "user:qq:1", "朋友", "user:qq:2",
            subject_label="阿辰", object_label="老王", evidence="常一起吃饭",
        )
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        projected = len(client.items)
        assert projected == 2

        # 同一条边以相同参数重复写入（ON CONFLICT 仅刷强度/证据，值不变）：
        # 两端节点邻域文档指纹未变 → 消费侧跳过，不重跑 add/cognify
        await store.graph.add_relation(
            "user:qq:1", "朋友", "user:qq:2", evidence="常一起吃饭",
        )
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))

        assert len(client.items) == projected
        assert (await store.get_cognee_sync_status())["pending"] == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_graph_strength_drift_skips_reprojection(tmp_path) -> None:
    """结构指纹不含强度/证据：关系被重复提及强化时不再整篇重投影。"""
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = _GraphFakeClient()
    coordinator = CogneeCoordinator(
        store, client, CogneeConfig(enabled=True, sync_enabled=True),
    )
    try:
        await store.graph.add_relation("user:qq:1", "朋友", "user:qq:2", strength=0.5)
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        assert len(client.items) == 2

        # 强度强化 + 证据刷新：文档文本变了但邻域结构未变 → 跳过重投影
        await store.graph.add_relation(
            "user:qq:1", "朋友", "user:qq:2", strength=0.9, evidence="新证据",
        )
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        assert len(client.items) == 2

        # 邻域结构真实变化（新增边）：仍正常重投影
        await store.graph.add_relation("user:qq:1", "同事", "user:qq:3")
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        assert len(client.items) == 4
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_goal_memory_is_not_projected(tmp_path) -> None:
    """计划状态 JSON 不投影 cognee；存量映射转为 delete 清理。"""
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        # 新增 goal 记忆：不产生任何投影队列条目
        goal_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content='{"goal_id": "g1"}', source="goal",
        ))
        assert await store.claim_cognee_sync_batch(10) == []
        assert await store.get_cognee_mapping(goal_id) is None

        # 模拟历史遗留：已有投影映射的 goal 再次写入 → 转为 delete 清理旧投影
        normal_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="normal", source="chat",
        ))
        item = (await store.claim_cognee_sync_batch(10))[0]
        await store.complete_cognee_sync(
            item["queue_id"], normal_id,
            dataset_name="anelf_global", dataset_id="d", data_id="dd",
            content_hash="x",
        )
        db = await store._get_db()
        async with store._tx(db):
            await db.execute(
                "UPDATE memories SET source='goal' WHERE id=?", (normal_id,),
            )
        entry = await store.get(normal_id)
        assert entry is not None
        entry.content = "updated goal"
        await store.update(entry)
        batch = await store.claim_cognee_sync_batch(10)
        assert len(batch) == 1
        assert batch[0]["operation"] == "delete"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_dedup_noop_update_skips_write_and_projection(tmp_path) -> None:
    """合并内容与标签均无变化：不落库（version/审计不动）、不触发投影。"""
    from agent.memory.dedup import apply_update

    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    try:
        memory_id = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="原内容", tags=["user:1"],
        ))
        await store.claim_cognee_sync_batch(10)

        result = await apply_update(store, memory_id, "原内容", extra_tags=["user:1"])

        entry = await store.get(memory_id)
        assert result is not None
        assert entry is not None
        assert entry.version == 1
        assert await store.claim_cognee_sync_batch(10) == []
    finally:
        await store.close()


# ==================================================================
# 写盘熔断
# ==================================================================

def test_write_breaker_trips_on_burst() -> None:
    from agent.memory.cognee.write_breaker import WriteBreaker

    base_ns = 1_000_000_000
    writes = iter([0, 300 * 1024 * 1024])
    times = iter([base_ns, base_ns + 10_000_000_000])
    breaker = WriteBreaker(
        100.0, 60.0, sampler=lambda: next(writes), clock=lambda: next(times),
    )

    breaker.observe()
    breaker.observe()

    tripped, rate_mb_s = breaker.over_threshold()
    assert tripped  # 300MB/10s = 30MB/s > 100MB/60s
    assert rate_mb_s > 100.0 / 60.0


def test_write_breaker_allows_normal_rate() -> None:
    from agent.memory.cognee.write_breaker import WriteBreaker

    base_ns = 1_000_000_000
    writes = iter([0, 10 * 1024 * 1024])
    times = iter([base_ns, base_ns + 10_000_000_000])
    breaker = WriteBreaker(
        100.0, 60.0, sampler=lambda: next(writes), clock=lambda: next(times),
    )

    breaker.observe()
    breaker.observe()

    tripped, _ = breaker.over_threshold()
    assert not tripped  # 1MB/s < 100MB/60s ≈ 1.67MB/s


def test_write_breaker_unavailable_sampler_disables() -> None:
    from agent.memory.cognee.write_breaker import WriteBreaker

    def _boom() -> int:
        raise OSError("io_counters unavailable")

    breaker = WriteBreaker(100.0, 60.0, sampler=_boom, clock=lambda: 0)

    breaker.observe()

    assert not breaker.available
    assert breaker.over_threshold() == (False, 0.0)


def test_write_breaker_window_evicts_stale_samples() -> None:
    from agent.memory.cognee.write_breaker import WriteBreaker

    second = 1_000_000_000
    writes = iter([0, 10 * 1024 ** 3, 10 * 1024 ** 3 + 1024 ** 2])
    times = iter([0, 50 * second, 70 * second])
    breaker = WriteBreaker(
        100.0, 60.0, sampler=lambda: next(writes), clock=lambda: next(times),
    )

    breaker.observe()
    breaker.observe()
    breaker.observe()

    # 从窗口起点算是风暴（10GB/70s），窗口内只剩 1MB/20s → 不熔断
    tripped, _ = breaker.over_threshold()
    assert not tripped


@pytest.mark.asyncio
async def test_projection_allowed_pauses_on_write_storm(tmp_path) -> None:
    from agent.memory.cognee.write_breaker import WriteBreaker

    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    client = _FakeCogneeClient()
    coordinator = CogneeCoordinator(
        store,
        client,
        CogneeConfig(
            enabled=True, sync_enabled=True,
            write_breaker_threshold_mb=100.0, write_breaker_window_seconds=60.0,
        ),
    )
    second = 1_000_000_000
    writes = iter([0, 10 * 1024 ** 3])
    times = iter([second, 11 * second])
    coordinator._breaker = WriteBreaker(
        100.0, 60.0, sampler=lambda: next(writes), clock=lambda: next(times),
    )
    try:
        assert coordinator._projection_allowed() is True  # 首个样本不足以判定
        assert coordinator._projection_allowed() is False  # 10GB/10s → 熔断暂停

        status = await coordinator.status()
        assert status.paused
        assert status.paused_until > 0
        assert "超阈值" in status.pause_reason
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_projection_allowed_ignores_disabled_breaker(tmp_path) -> None:
    from agent.memory.cognee.write_breaker import WriteBreaker

    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    client = _FakeCogneeClient()
    coordinator = CogneeCoordinator(
        store,
        client,
        CogneeConfig(
            enabled=True, sync_enabled=True, write_breaker_enabled=False,
            write_breaker_threshold_mb=100.0, write_breaker_window_seconds=60.0,
        ),
    )
    second = 1_000_000_000
    writes = iter([0, 10 * 1024 ** 3])
    times = iter([second, 11 * second])
    coordinator._breaker = WriteBreaker(
        100.0, 60.0, sampler=lambda: next(writes), clock=lambda: next(times),
    )
    try:
        assert coordinator._projection_allowed() is True
        assert coordinator._projection_allowed() is True

        status = await coordinator.status()
        assert not status.paused
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
    assert first[1] == "test_relations"  # 全局关系数据集（自由型节点）对所有 scope 开放
    assert len(first) == 4
    assert "sensitive-id" not in first[2]
    assert first[2] != second[2]
    # 实体型节点的 per-scope 关系投影数据集与记忆数据集同构派生
    assert first[3] == f"test_relations_{first[2].removeprefix('test_')}"
    assert first[3] != second[3]


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
            snippet="The user prefers concise answers.",
            provenance={"anelf_memory_id": 7},
            score=0.95,
            source="cognee_chunk",
        ),
    ]

    results = reciprocal_rank_fusion(native, projected, config=config, limit=5)

    assert len(results) == 1
    assert results[0].source == "memory"
    assert results[0].score == 1.0


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


def test_compaction_preserves_retained_versions_and_rotates_shared_budget(tmp_path, monkeypatch) -> None:
    lancedb = pytest.importorskip("lancedb")
    import cognee_db_workers.lancedb_compaction as compaction

    from agent.memory.cognee.storage import compact_lance_tree

    db_dir = tmp_path / "system" / "databases" / "probe.lance.db"
    connection = lancedb.connect(str(db_dir))
    versions: dict[str, set[int]] = {}
    for name in ("first", "second"):
        table = connection.create_table(name, [{"id": 0, "vector": [0.0] * 4}])
        for value in range(1, 5):
            table.add([{"id": value, "vector": [float(value)] * 4}])
        versions[name] = {row["version"] for row in table.list_versions()}

    monkeypatch.setattr(compaction, "DEFAULT_MAX_TASKS_PER_RUN", 1)
    monkeypatch.setattr(compaction, "DEFAULT_MAX_VERSIONS_PER_RUN", 2)
    retained = compact_lance_tree(db_dir.parent, 1.0)
    assert retained["errors"] == []
    assert retained["executed_tasks"] == 1
    assert retained["old_versions_removed"] == 0
    for name in versions:
        table = connection.open_table(name)
        assert versions[name] <= {row["version"] for row in table.list_versions()}
        assert table.count_rows() == 5
    assert len(connection.open_table("first").to_lance().get_fragments()) == 1
    assert len(connection.open_table("second").to_lance().get_fragments()) == 5

    rotated = compact_lance_tree(db_dir.parent, 0.0, rotation=1)
    assert rotated["errors"] == []
    assert rotated["executed_tasks"] == 1
    assert rotated["old_versions_removed"] == 2
    assert rotated["budget_exhausted"] is True
    assert len(connection.open_table("second").to_lance().get_fragments()) == 1
    assert connection.open_table("second").count_rows() == 5


async def test_compaction_cancel_drains_native_write_before_unlocking(store, tmp_path, monkeypatch) -> None:
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    def compact(*args: Any, **kwargs: Any) -> dict[str, Any]:
        loop.call_soon_threadsafe(started.set)
        if not release.wait(5):
            raise TimeoutError("test did not release native writer")
        return {}

    monkeypatch.setattr("agent.memory.cognee.storage.compact_lance_tree", compact)
    config = CogneeConfig(data_root=str(tmp_path), write_breaker_enabled=False)
    client = CogneeClient(config)
    client._configured = True
    coordinator = CogneeCoordinator(store, client, config)
    task = asyncio.create_task(coordinator._run_compact())
    try:
        await asyncio.wait_for(started.wait(), 3)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert coordinator._projection_lock.locked()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not coordinator._projection_lock.locked()
    assert client._compact_rotation == 1


async def test_upstream_compaction_is_owned_by_host(tmp_path, monkeypatch) -> None:
    from cognee.infrastructure.databases.vector.lancedb.LanceDBAdapter import LanceDBAdapter

    from agent.memory.cognee.adapters import _host_compaction_reason

    monkeypatch.setattr(LanceDBAdapter, "_compaction_skip_reason", _host_compaction_reason)
    adapter = LanceDBAdapter(str(tmp_path), None, Mock())
    assert await adapter.compact() == {"skipped": "host_managed"}
    assert adapter.connection is None


@pytest.mark.parametrize("operation", ["improve", "idle_inline", "idle_queued"])
async def test_manual_maintenance_cannot_overlap_projection(store, operation) -> None:
    client = _FakeCogneeClient()
    client.improve = AsyncMock(return_value="done")
    coordinator = CogneeCoordinator(store, client, CogneeConfig(write_breaker_enabled=False))
    async with coordinator._projection_lock:
        if operation == "improve":
            task = asyncio.create_task(coordinator.improve("dataset"))
        elif operation == "idle_inline":
            task = asyncio.create_task(coordinator.run_in_idle_window(client.improve()))
        else:
            coordinator._idle_job = client.improve()
            task = asyncio.create_task(coordinator._run_idle_job())
        await asyncio.sleep(0)
        assert not task.done()
        client.improve.assert_not_awaited()
    await task
    client.improve.assert_awaited_once()


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

    await asyncio.wait_for(asyncio.gather(*tracker._tasks), timeout=5)
    assert (await tracker.get(str(tmp_path)))["total_bytes"] == 100
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
    await asyncio.wait_for(asyncio.gather(*tracker._tasks), timeout=5)
    assert (await tracker.get(str(tmp_path)))["total_bytes"] != 500

    tracker.invalidate(str(tmp_path))
    assert not (tmp_path / "storage_stats.json").exists()
    assert (await tracker.get(str(tmp_path)))["total_bytes"] == 0
    await asyncio.wait_for(asyncio.gather(*tracker._tasks), timeout=5)


@pytest.mark.asyncio
async def test_storage_stats_adopt_wins_over_inflight_refresh(
    tmp_path, monkeypatch,
) -> None:
    """在途遍历期间发生 adopt（压缩实测值）：代际递增使旧遍历结果被丢弃。"""
    import asyncio
    import threading

    from agent.memory.cognee import storage

    gate = threading.Event()
    started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def slow_compute(_root):
        loop.call_soon_threadsafe(started.set)
        gate.wait(10)
        return storage.StorageStatsDict(
            total_bytes=100, data_bytes=0, lance_bytes=100,
            graph_bytes=0, metadata_bytes=0, other_bytes=0,
        )

    monkeypatch.setattr(storage, "compute_storage_stats", slow_compute)
    tracker = storage.StorageStatsTracker(ttl_seconds=0.0)
    root = str(tmp_path)

    tracker.schedule_refresh(root)
    await asyncio.wait_for(started.wait(), timeout=5)
    fresh = storage.StorageStatsDict(
        total_bytes=500, data_bytes=0, lance_bytes=500,
        graph_bytes=0, metadata_bytes=0, other_bytes=0,
    )
    tracker.adopt(root, fresh)
    gate.set()
    await asyncio.wait_for(asyncio.gather(*tracker._tasks), timeout=5)

    assert tracker._cache[root][1]["total_bytes"] == 500  # 未被在途旧遍历覆盖
    assert json.loads((tmp_path / "storage_stats.json").read_text())["stats"]["total_bytes"] == 500


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
    """空闲窗口按间隔自动压缩；关闭自动维护不影响显式请求。"""
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
            write_breaker_enabled=False,
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
        await coordinator._maybe_compact()
        assert client.compact_calls == [3.0, 3.0]
        coordinator._compact_requested = True
        await coordinator._maybe_compact()
        assert client.compact_calls == [3.0, 3.0, 3.0]
        assert coordinator._compact_requested is False
        await asyncio.sleep(0.05)  # 等 adopt 的快照保存任务收尾
    finally:
        await store.close()


# ==================================================================
# 定向实体检索（node_name 通道）
# ==================================================================

class _TargetingFakeClient:
    """定向检索假客户端：记录 recall 调用参数。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def initialize(self):
        return SimpleNamespace(ready=True)

    async def available_datasets(self, names):
        return names

    def search_type(self, name):
        return str(name).upper()

    async def recall(self, query, **kwargs):
        self.calls.append(kwargs)
        return []


@pytest.mark.asyncio
async def test_search_cognee_targeted_lane_uses_node_names() -> None:
    from agent.memory.cognee.fusion import search_cognee

    client = _TargetingFakeClient()
    config = CogneeConfig(enabled=True)
    await search_cognee(
        client, config, "查询", ["anelf_global"], 5,
        ["CHUNKS"], node_names=["阿辰", "老王"],
    )
    targeted = [c for c in client.calls if c.get("node_name")]
    # 定向通道每数据集一次（不随 search_types 翻倍），node_name 透传 cognee
    assert len(targeted) == 1
    assert targeted[0]["node_name"] == ["阿辰", "老王"]
    assert targeted[0]["top_k"] == max(5, 5 * config.recall_pool_multiplier)
    # 常规通道不带 node_name
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_search_cognee_without_node_names_skips_targeted_lane() -> None:
    from agent.memory.cognee.fusion import search_cognee

    client = _TargetingFakeClient()
    await search_cognee(
        client, CogneeConfig(enabled=True), "查询", ["anelf_global"], 5, ["CHUNKS"],
    )
    assert all("node_name" not in c for c in client.calls)


# ==================================================================
# 投影文档解析（fusion 边界：干净正文 + 标签回填）
# ==================================================================

class _ProjectionFakeClient(_TargetingFakeClient):
    """返回带投影文档头的 chunk，验证边界解析。"""

    def __init__(self):
        super().__init__()
        self._items = None

    async def recall(self, query, **kwargs):
        if self._items is None:
            from agent.memory.cognee.types import CogneeRecallItem
            self._items = [
                CogneeRecallItem(
                    id="cognee:1",
                    content=(
                        "Memory type: semantic\nSource: entity_1\nImportance: 0.6\n"
                        "Tags: user:qq:1, topic:火锅\nMetadata: {}\n\n阿辰喜欢火锅"
                    ),
                    score=0.9,
                    source="cognee_chunk",
                    dataset_name="anelf_global",
                ),
            ]
        return self._items


@pytest.mark.asyncio
async def test_search_cognee_parses_projection_and_backfills_tags() -> None:
    from agent.memory.cognee.fusion import search_cognee

    client = _ProjectionFakeClient()
    results = await search_cognee(
        client, CogneeConfig(enabled=True), "查询", ["anelf_global"], 5, ["CHUNKS"],
    )
    assert len(results) == 1
    r = results[0]
    # 正文剥离头部字段（无 "Memory type:" 噪音），标签回填（归属标注/加权可用）
    assert r.snippet == "阿辰喜欢火锅"
    assert r.tags == ["user:qq:1", "topic:火锅"]


async def test_projection_failure_keeps_legacy_data_and_permanent_error_stops_retry(store):
    store.set_cognee_projection_enabled(True)
    entry_id = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content="new version"))
    items = await store.claim_cognee_sync_batch(10)
    await store.complete_cognee_sync(items[0]["queue_id"], entry_id, dataset_name="anelf_global",
        dataset_id="dataset-id", data_id="legacy-id", content_hash="old")
    entry = await store.get(entry_id)
    entry.content = "changed version"
    await store.update(entry)
    items = await store.claim_cognee_sync_batch(10)
    client = _FakeCogneeClient()
    client.cognify = AsyncMock(side_effect=ValueError("invalid extraction schema"))
    coordinator = CogneeCoordinator(store, client, CogneeConfig())
    await coordinator._process_batch(items)
    assert not client.deleted
    assert (await store.get_cognee_mapping(entry_id))["data_id"] == "legacy-id"
    assert (await store.get_cognee_sync_status())["failed"] == 1
    client.cognify = AsyncMock()
    await store.retry_failed_cognee_sync()
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    assert client.deleted == [("dataset-id", "legacy-id")]
    assert (await store.get_cognee_mapping(entry_id))["data_id"] == f"data-{entry_id}"


async def test_cognify_timeout_keeps_projection_pending_and_retries_same_document(store, monkeypatch):
    from cognee.modules.pipelines.exceptions import CognifyFailedError

    store.set_cognee_projection_enabled(True)
    entry_id = await store.add(MemoryEntry(content="pending extraction", memory_type=MemoryType.SEMANTIC))
    client = _FakeCogneeClient()
    client.cognify = AsyncMock(side_effect=CognifyFailedError(
        dataset_name="anelf_global", error_class="TimeoutError", error_message=None,
    ))
    warning = Mock()
    monkeypatch.setattr("agent.memory.cognee.coordinator.log", warning)
    coordinator = CogneeCoordinator(store, client, CogneeConfig())
    items = await store.claim_cognee_sync_batch(10)
    await coordinator._process_batch(items)
    assert await store.get_cognee_mapping(entry_id) is None
    assert await store.get_cognee_sync_status() == {"pending": 1, "failed": 0, "synced": 0}
    assert not await store.claim_cognee_sync_batch(10)
    assert coordinator._last_error.count("CognifyFailedError:") == 1
    assert "TimeoutError" in coordinator._last_error
    assert "422" not in coordinator._last_error
    assert "raise_on_error=False" not in coordinator._last_error
    assert "stage:cognify" in warning.call_args.args[0]
    assert "耗时" in warning.call_args.args[0]

    retry_time = time.time_ns() + 2_000_000_000
    monkeypatch.setattr("agent.memory.store.cognee_queue.time.time_ns", lambda: retry_time)
    client.cognify = AsyncMock()
    await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
    assert client.items[0]["data_id"] == client.items[1]["data_id"]
    assert await store.get_cognee_sync_status() == {"pending": 0, "failed": 0, "synced": 1}
    assert coordinator._last_error == ""


def test_existing_embedding_data_adopts_identity_without_rebuilding(tmp_path):
    from agent.memory.cognee.embedding_identity import validate_embedding

    artifact = tmp_path / "system" / "old-vector-data"
    artifact.parent.mkdir()
    artifact.write_bytes(b"preserve")
    payload = {"embedding_model": "openai/embed", "embedding_provider": "openai",
               "embedding_endpoint": "https://example.invalid/v1", "embedding_dimensions": 1024}
    validate_embedding(tmp_path, payload, record=True)
    validate_embedding(tmp_path, {**payload, "embedding_api_key": "rotated"}, record=True)
    validate_embedding(tmp_path, {**payload, "embedding_dimensions": 0})
    for changes in ({"embedding_model": "other"}, {"embedding_dimensions": 2048}):
        with pytest.raises(CogneeConfigError, match="不兼容"):
            validate_embedding(tmp_path, {**payload, **changes}, record=True)
    assert artifact.read_bytes() == b"preserve"
    assert json.loads((tmp_path / "anelf-embedding.json").read_text())["model"] == "embed"


def test_retry_policy_rejects_permanent_nested_failures():
    from agent.memory.cognee.adapters import retryable

    wrapped = RuntimeError("pipeline failed")
    wrapped.__cause__ = ValueError("invalid JSON")
    assert not retryable(wrapped)
    assert not retryable(asyncio.CancelledError())
    assert retryable(TimeoutError("temporary"))


@pytest.mark.parametrize("error_class,expected", [("TimeoutError", True), ("AuthenticationError", False)])
async def test_pipeline_retry_uses_root_cause_instead_of_wrapper_status(monkeypatch, error_class, expected):
    from agent.memory.cognee.adapters import retryable
    from agent.memory.cognee.types import CogneePipelineError

    wrapper = type("CognifyFailedError", (RuntimeError,), {"status_code": 422, "error_class": error_class})
    assert retryable(wrapper("cognify failed")) is expected
    wrapped = type("PipelineRunFailedError", (RuntimeError,), {"status_code": 422})("pipeline failed")
    wrapped.first_error = TimeoutError("network")
    assert retryable(wrapped)

    client = CogneeClient(CogneeConfig())
    monkeypatch.setattr(client, "_call", AsyncMock(return_value=SimpleNamespace(
        status="PipelineRunErrored", error_class=error_class, error_message="root cause",
    )))
    with pytest.raises(CogneePipelineError) as caught:
        await client.add([])
    assert retryable(caught.value) is expected
    assert error_class in str(caught.value)


def test_disabling_cognee_does_not_require_a_valid_embedding(monkeypatch):
    monkeypatch.setattr("agent.memory.cognee.client.resolve_embedding_llm_config",
                        Mock(side_effect=AssertionError("disabled configuration does not write vectors")))
    CogneeClient(CogneeConfig()).validate_configuration(CogneeConfig(enabled=False))


async def test_projection_ids_are_stable_and_isolated_by_dataset(monkeypatch, tmp_path):
    client = CogneeClient(CogneeConfig(data_root=str(tmp_path)))
    client._module = SimpleNamespace()
    client._configured = True
    monkeypatch.setattr("agent.memory.cognee.client.importlib.import_module",
        lambda name: SimpleNamespace(DataItem=lambda data, **kwargs: SimpleNamespace(data=data, **kwargs)))
    async def item(data, dataset):
        return await client.make_data_item(data, label="anelf-memory-1", external_metadata={}, dataset_name=dataset)
    first = await item("old", "global")
    assert first.literal_text is True
    assert (await item("old", "global")).data_id == first.data_id
    assert (await item("new", "global")).data_id != first.data_id
    assert (await item("old", "private")).data_id != first.data_id


async def test_embedding_transport_does_not_change_host_drop_policy():
    from agent.memory.cognee.adapters import _EmbeddingTransport

    module = SimpleNamespace(aembedding=AsyncMock(return_value=[[1.0]]), drop_params=False)
    proxy = _EmbeddingTransport(module)
    assert await proxy.aembedding(model="test", input=["query"]) == [[1.0]]
    assert module.aembedding.call_args.kwargs["drop_params"] is True
    assert module.drop_params is False


async def test_reconfigure_drains_projection_before_switching_config(store, monkeypatch):
    client = _FakeCogneeClient()
    client.reconfigure = Mock()
    coordinator = CogneeCoordinator(store, client, CogneeConfig())
    started, release = asyncio.Event(), asyncio.Event()

    async def process(batch):
        started.set()
        await release.wait()

    monkeypatch.setattr(coordinator, "_process_entries", process)
    batch = asyncio.create_task(coordinator._process_batch([]))
    await started.wait()
    await asyncio.wait_for(coordinator.reconfigure(CogneeConfig(enabled=False)), timeout=0.2)
    switch = coordinator._configuration_task
    assert switch is not None
    try:
        await asyncio.sleep(0)
        assert coordinator.configuration_pending
        client.reconfigure.assert_not_called()
        await coordinator.reconfigure(CogneeConfig(enabled=False, dataset_prefix="latest"))
        release.set()
        await asyncio.gather(batch, switch)
        client.reconfigure.assert_called_once()
        assert not coordinator.config.enabled
        assert coordinator.config.dataset_prefix == "latest"
        assert not coordinator.configuration_pending
    finally:
        for task in (batch, switch):
            task.cancel()
        await asyncio.gather(batch, switch, return_exceptions=True)


async def test_close_cancels_pending_configuration(store: MemoryStore) -> None:
    client = _FakeCogneeClient()
    coordinator = CogneeCoordinator(store, client, CogneeConfig())
    async with coordinator._projection_lock:
        await coordinator.reconfigure(CogneeConfig(enabled=False))
        await asyncio.sleep(0)
        await asyncio.wait_for(coordinator.close(), timeout=0.2)
    assert not coordinator.configuration_pending
    assert coordinator.config.enabled


async def test_configuration_failure_is_visible_and_recoverable(store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    coordinator = CogneeCoordinator(store, _FakeCogneeClient(), CogneeConfig())
    apply = AsyncMock(side_effect=RuntimeError("invalid extraction configuration"))
    monkeypatch.setattr(coordinator, "_apply_configuration", apply)
    await coordinator.reconfigure(CogneeConfig(sync_enabled=False))
    await coordinator._configuration_task
    status = await coordinator.status()
    assert not status.configuration_pending
    assert "invalid extraction configuration" in status.configuration_error
    apply.side_effect = None
    await coordinator.reconfigure(CogneeConfig(sync_enabled=False))
    await coordinator._configuration_task
    assert not (await coordinator.status()).configuration_error
