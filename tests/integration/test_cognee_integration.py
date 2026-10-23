from __future__ import annotations

import asyncio
import json
import os

import pytest

from agent.memory.cognee.client import CogneeClient
from agent.memory.cognee.config import load_cognee_config
from agent.memory.cognee.coordinator import CogneeCoordinator
from agent.memory.cognee.fusion import search_cognee
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("COGNEE_INTEGRATION") != "1",
        reason="set COGNEE_INTEGRATION=1 with working model credentials",
    ),
]


@pytest.mark.timeout(900)
async def test_real_coverage_repair_api_validation_and_old_generation_retirement(tmp_path, monkeypatch):
    """真实模型与索引贯通 HTTP 修复、同轮验收、旧轮清理及当前图召回。"""
    import httpx
    from fastapi import FastAPI

    from web.routers.memory import router

    config = load_cognee_config()
    config.enabled = config.sync_enabled = True
    config.project_memories_enabled = config.project_graph_enabled = True
    config.data_root = str(tmp_path / "cognee")
    config.timeout_seconds = config.pipeline_timeout_seconds = 180
    config.write_breaker_enabled = False
    config.improve_interval_seconds = 0
    monkeypatch.setattr("agent.memory.cognee.rebuild.backups_root", lambda: tmp_path / "backups")
    monkeypatch.setattr("agent.memory.cognee.retirement.backups_root", lambda: tmp_path / "backups")
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = CogneeClient(config, source_mappings=store.projection_state.publication_sources)
    coordinator = CogneeCoordinator(store, client, config)
    monkeypatch.setattr("agent.memory.cognee.runtime.get_cognee_coordinator", lambda: coordinator)
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def finish():
        for _ in range(8):
            await coordinator.rebuild.advance()
            batch = await store.claim_cognee_sync_batch(10)
            if batch:
                await coordinator._process_batch(batch)
            assert not coordinator.writer.last_error, coordinator.writer.last_error
            assert coordinator.rebuild.state.phase != "failed", coordinator.rebuild.state.error
            if coordinator.rebuild.state.phase == "ready":
                return
        pytest.fail("真实索引未完成验收")

    try:
        await coordinator.rebuild.initialize(config)
        mid = await store.add(MemoryEntry(content="蓝鸢项目由林舟维护，部署在杭州。", memory_type=MemoryType.SEMANTIC))
        await store.graph.add_relation("project:蓝鸢", "维护者", "person:林舟", subject_label="蓝 鸢",
                                       evidence="林舟负责蓝鸢项目的发布和维护。")
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        assert not coordinator.writer.last_error
        await coordinator.rebuild.request(config)
        await finish()
        state = coordinator.rebuild.state
        namespace, old = state.namespace, list(state.previous_namespaces)
        memory_before = await store.get_cognee_mapping(mid)
        db = await store._conn.get_db()
        async with store._conn.tx(db):
            await db.execute("UPDATE graph_nodes SET label='蓝鸢' WHERE node_key='project:蓝鸢'")
        state.phase = "building"
        await coordinator.rebuild.advance()
        assert state.phase == "failed" and not client.projection_ready
        config.sync_enabled = False
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            wrong = await http.post("/api/memory/cognee/repair-coverage", params={"namespace": "old-page"})
            assert wrong.status_code == 409
            response = await http.post("/api/memory/cognee/repair-coverage", params={"namespace": namespace})
            assert response.status_code == 200, response.text
            assert response.json() == {"queued": 2, "namespace": namespace, "paused": True}
        config.sync_enabled = True
        await finish()
        assert state.namespace == namespace and client.projection_ready
        assert await store.get_cognee_mapping(mid) == memory_before
        config.sync_enabled = False
        for previous in old:
            await coordinator.retirement.request_generation(previous)
            await coordinator.retirement._task
            assert coordinator.retirement.status.stage == "completed", coordinator.retirement.status.error
        assert state.previous_namespaces == []
        assert (await store.get(mid)).content == "蓝鸢项目由林舟维护，部署在杭州。"
        await coordinator.rebuild.verify_backend(state, await store.projection_state.mappings())
        rows = await client.recall("谁维护蓝鸢项目？", query_type=client.search_type("GRAPH_COMPLETION"),
            datasets=[namespace + "_relations"], only_context=True, include_references=True, auto_route=False)
        assert any("林舟" in row.content for row in rows), rows
    finally:
        await client.close()
        await store.close()


@pytest.mark.timeout(900)
async def test_real_relation_fragments_incremental_update_preserves_committed_documents(tmp_path, monkeypatch):
    """真实模型和图/向量库验证分片增量、旧文档留存、变更提交与逐项验收。"""
    from agent.memory.graph import fragments
    from agent.memory.projection_documents import mapped_documents

    config = load_cognee_config()
    config.enabled = config.sync_enabled = True
    config.project_memories_enabled = config.project_graph_enabled = True
    config.data_root = str(tmp_path / "cognee")
    config.timeout_seconds = config.pipeline_timeout_seconds = 180
    config.write_breaker_enabled = False
    config.improve_interval_seconds = 0
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = CogneeClient(config, source_mappings=store.projection_state.publication_sources)
    coordinator = CogneeCoordinator(store, client, config)
    monkeypatch.setattr(fragments, "MAX_DOCUMENT_CHARS", 120)
    monkeypatch.setattr(fragments, "EDGES_PER_RANGE", 1)
    writes: list[list[str]] = []
    original_add = client.add

    async def record_add(data, **kwargs):
        writes.append([item.data_id for item in data])
        return await original_add(data, **kwargs)

    monkeypatch.setattr(client, "add", record_add)

    async def drain():
        batch = await store.claim_cognee_sync_batch(10)
        await coordinator._process_batch(batch)
        status = await store.get_cognee_sync_status()
        assert status["pending"] == status["failed"] == 0, coordinator.writer.last_error
        audit = await store.projection_state.audit()
        assert not audit["missing_or_stale"] and not audit["extra"]
        await coordinator.rebuild.verify_backend(coordinator.rebuild.state, await store.projection_state.mappings())

    try:
        await coordinator.rebuild.initialize(config)
        await store.graph.add_relation("project:蓝鸢", "部署于", "place:杭州",
            evidence="蓝鸢服务部署在杭州机房，该地点是生产服务的实际运行位置。")
        await store.graph.add_relation("project:蓝鸢", "维护者", "person:林舟",
            evidence="林舟负责蓝鸢项目的日常发布、监控、故障处理和维护工作。")
        node_id = (await store.graph.get_node("project:蓝鸢"))["id"]
        with monkeypatch.context() as patch:
            patch.setattr(fragments, "MAX_DOCUMENT_CHARS", 10000)
            await drain()
        old = await store.get_cognee_mapping(node_id, entry_kind="graph_node")
        assert not mapped_documents(old)
        await store.graph.add_relation("project:蓝鸢", "部署于", "place:杭州",
            evidence="蓝鸢生产服务部署在杭州东区机房，由当地运维人员维护服务器。")
        await drain()
        mapping = await store.get_cognee_mapping(node_id, entry_kind="graph_node")
        parts = mapped_documents(mapping)
        assert len(parts) >= 2
        observed = await client.pipeline_progress(mapping["dataset_id"], {part.data_id: True for part in parts})
        assert not observed.observation_error
        assert observed.documents.total == observed.documents.completed == len(parts)
        assert observed.documents.fragments_completed == len(parts) and observed.documents.last_completed_at
        assert old["data_id"] not in {row["id"] for row in await client.list_data(mapping["dataset_id"])}
        writes.clear()
        await store.graph.add_relation("project:蓝鸢", "部署于", "place:杭州",
            evidence="蓝鸢生产服务部署在杭州西区机房，由当地运维人员维护服务器。")
        await drain()
        current = mapped_documents(await store.get_cognee_mapping(node_id, entry_kind="graph_node"))
        retained = {part.data_id for part in parts} & {part.data_id for part in current}
        assert retained and not retained.intersection(identity for batch in writes for identity in batch)
        documents = await client.list_data(mapping["dataset_id"])
        assert retained <= {row["id"] for row in documents}
        # 断开重开仍可依据同一清单验证所有分片，不产生新代际。
        await client.close()
        client = CogneeClient(config, source_mappings=store.projection_state.publication_sources)
        reopened = CogneeCoordinator(store, client, config)
        await reopened.rebuild.initialize(config)
        await reopened.rebuild.verify_backend(reopened.rebuild.state, await store.projection_state.mappings())
    finally:
        await client.close()
        await store.close()


async def test_real_ladybug_close_drains_cancelled_query_before_reopen(tmp_path):
    """真实 native 写入仍在途时取消等待方，关闭必须排空后才能重新打开文件。"""
    import threading

    from cognee.infrastructure.databases.graph.ladybug.adapter import LadybugAdapter

    from agent.memory.cognee import ladybug_guard
    from agent.memory.cognee.config import CogneeConfig

    ladybug_guard.install(lambda: CogneeConfig())
    path = str(tmp_path / "shutdown.lbug")
    adapter = LadybugAdapter(path, kuzu_num_threads=2, kuzu_buffer_pool_size=64 * 1024 * 1024)
    started, release = threading.Event(), threading.Event()
    try:
        await adapter.query("CREATE NODE TABLE ShutdownProbe(id STRING PRIMARY KEY)")

        def native_write():
            started.set()
            assert release.wait(10)
            adapter.connection.execute("CREATE (:ShutdownProbe {id: 'retained'})")

        query = asyncio.wrap_future(adapter._submit_to_executor_locked(native_write))
        assert await asyncio.to_thread(started.wait, 5)
        query.cancel()
        closing = asyncio.create_task(adapter.close())
        await asyncio.sleep(0.05)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert adapter.connection is not None
        release.set()
        await asyncio.wait_for(adapter.close(), 10)
    finally:
        release.set()
        await adapter.close()
    reopened = LadybugAdapter(path, kuzu_num_threads=2, kuzu_buffer_pool_size=64 * 1024 * 1024)
    try:
        assert await reopened.query("MATCH (n:ShutdownProbe) RETURN n.id") == [("retained",)]
    finally:
        await reopened.close()


@pytest.mark.timeout(600)
async def test_real_projection_nodeset_collisions_rebuild_and_exact_recall(tmp_path, monkeypatch):
    """真实模型、SDK 和索引校验共同覆盖标签合并、构建切换与精确召回。"""
    from agent.memory.tag_query import TagQuery

    config = load_cognee_config()
    config.enabled = config.sync_enabled = True
    config.project_memories_enabled = config.project_graph_enabled = True
    config.data_root = str(tmp_path / "cognee")
    config.timeout_seconds, config.pipeline_timeout_seconds = 120, 360
    config.write_breaker_enabled = False
    monkeypatch.setattr("agent.memory.cognee.rebuild.backups_root", lambda: tmp_path / "backups")
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(True)
    client = CogneeClient(config, source_mappings=store.projection_state.publication_sources)
    coordinator = CogneeCoordinator(store, client, config)
    tags = ["topic:WOL", "topic:WoL", "project:two words", "project:two_words", "owner:O'Brien", "owner:OBrien"]
    try:
        await coordinator.rebuild.initialize(config)
        mid = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC,
            content="蓝鸢项目部署在杭州。林舟负责蓝鸢项目的远程唤醒功能。", tags=tags))
        await coordinator.rebuild.request(config)
        for _ in range(8):
            await coordinator.rebuild.advance()
            batch = await store.claim_cognee_sync_batch(10)
            if batch:
                await coordinator._process_batch(batch)
            status = await coordinator.status()
            assert not status.last_error and not status.failed, status.last_error
            assert coordinator.rebuild.state.phase != "failed", coordinator.rebuild.state.error
            if coordinator.rebuild.state.phase == "ready":
                break
        assert coordinator.rebuild.state.phase == "ready"
        assert coordinator.rebuild.state.verified == 1
        assert (await store.get(mid)).tags == tags
        mapping = await store.get_cognee_mapping(mid)
        rows = await client.list_data(mapping["dataset_id"])
        assert len(rows) == 1
        assert set(rows[0]["external_metadata"]["anelf_attribution"]["tags"]) == set(tags)
        for tag_query in (TagQuery(all_of=("topic:WoL",)), TagQuery(all_of=("topic:WOL", "topic:WoL"))):
            found = await search_cognee(client, config, "蓝鸢项目部署地点", [mapping["dataset_name"]], 1,
                                        ["CHUNKS"], tag_query=tag_query)
            assert any(item.memory_reference() == f"mem:{mid}" and set(item.tags) == set(tags) for item in found)
        excluded = await search_cognee(client, config, "蓝鸢项目", [mapping["dataset_name"]], 1,
            ["CHUNKS"], tag_query=TagQuery(all_of=("topic:WOL",), none_of=("topic:WoL",)))
        assert excluded == []
    finally:
        await client.close()
        await store.close()


@pytest.mark.parametrize("scenario,expected_tool,expected_arguments", [
    ("查一下发布安排，只查 project:蓝鸢 的记忆，排除 state:old 标签的旧记录。",
     "recall", {"filter_tags": "project:蓝鸢", "filter_not": "state:old"}),
    ("查清历史记忆中林舟、白鹭项目与蓝鸢项目的跨记录合作关系，需要多条事实关联起来。",
     "recall", {"depth": "deep"}),
    ("当前 Cognee 全局摘要已刷新。请综合概括蓝鸢项目整体背景、演进和参与者。",
     "recall", {"depth": "overview"}),
    ("召回结果 memory_id=7、id=mem:7，truncated=true。请先核实这条记忆的完整原文和来源。",
     "get_memory", {"memory_id": 7}),
])
async def test_real_model_selects_memory_tools(scenario, expected_tool, expected_arguments):
    """真实模型只生成调用，不执行维护或写入；工具 schema 与宿主注册共用。"""
    from agent.llm.llm_manager import LLMManager
    from agent.memory import tools as memory_tools
    from core.entity import EntityRegistry
    from core.tool_registry import activate_group

    assert memory_tools.recall is not None
    activate_group("memory")
    schemas = EntityRegistry.get_tool_schema_by_names([
        "recall", "get_memory", "memory_index", "cognee_status", "list_cognee_datasets", "improve_cognee_dataset",
    ])
    assert len(schemas) == 6
    manager = LLMManager()
    client = manager.get_chat_client(require_tools=True)
    assert client is not None
    try:
        answer = await client.chat([
            {"role": "system", "content": "根据工具说明，使用函数调用协议发出一个最适合完成请求的工具调用。工具由宿主执行，无需口头解释选择。"},
            {"role": "user", "content": scenario},
        ], tools=schemas, tool_choice="required", options={"max_tokens": 768, "temperature": 0, "parallel_tool_calls": False})
        # 附加只读目录查询是合法策略；拒绝重复昂贵召回或自动维护。
        assert 1 <= len(answer.tool_calls) <= 2, answer.content
        assert all(extra.name in {"memory_index", "list_cognee_datasets", "cognee_status"}
                   for extra in answer.tool_calls[1:]), [(item.name, item.arguments) for item in answer.tool_calls]
        call = answer.tool_calls[0]
        assert call.name == expected_tool
        arguments = json.loads(call.arguments)
        assert {key: arguments.get(key) for key in expected_arguments} == expected_arguments, arguments
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.timeout(600)
async def test_real_projection_rebuild_tagged_recall_update_delete(tmp_path, monkeypatch) -> None:
    """使用本地已配置模型、真实 Cognee 数据库和项目投影链，所有数据写入临时目录。"""
    config = load_cognee_config()
    config.enabled = config.sync_enabled = True
    config.project_memories_enabled = config.project_graph_enabled = True
    config.data_root = str(tmp_path / "cognee")
    config.timeout_seconds, config.pipeline_timeout_seconds = 120, 600
    config.write_breaker_enabled = False
    monkeypatch.setattr("agent.memory.cognee.rebuild.backups_root", lambda: tmp_path / "backups")
    monkeypatch.setattr("agent.memory.cognee.retirement.backups_root", lambda: tmp_path / "backups")
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    client = CogneeClient(config, source_mappings=store.projection_state.publication_sources)
    store.set_cognee_projection_enabled(True)
    coordinator = CogneeCoordinator(store, client, config)
    await coordinator.rebuild.initialize(config)
    availability = await client.initialize()
    assert availability.ready, availability.reason

    try:
        mid = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC,
            content="林舟负责蓝鸢项目。蓝鸢项目的部署地点是杭州。", tags=["project:蓝鸢", "user:test:linzhou", "future:tag"],
            metadata={"sensitivity": "private"}))
        shared = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC,
            content="林舟也负责白鹭项目。白鹭项目使用Rust语言。", tags=["project:白鹭", "user:test:linzhou"]))
        legacy_text = (f"Memory type: semantic\nMemory ID: {mid}\nSource: \nImportance: 0.5\n"
                       "Tags: project:蓝鸢\nMetadata: {}\n\n蓝鸢项目曾计划部署在南京。")
        orphan = await client.make_data_item(legacy_text, label=f"anelf-memory-{mid}",
            external_metadata={"anelf_memory_id": str(mid)}, dataset_name="anelf_global")
        await client.add([orphan], dataset_name="anelf_global", incremental_loading=True)
        other = await client.make_data_item(legacy_text, label=f"anelf-memory-{mid}",
            external_metadata={"anelf_memory_id": str(mid)}, dataset_name="anelf_relations")
        await client.add([other], dataset_name="anelf_relations", incremental_loading=True)
        await coordinator.rebuild.request(config)
        observed_progress = []
        for _ in range(8):
            await coordinator.rebuild.advance()
            batch = await store.claim_cognee_sync_batch(10)
            if batch:
                task = asyncio.create_task(coordinator._process_batch(batch))
                try:
                    while not task.done():
                        status = await coordinator.status()
                        if status.active and status.active.pipeline:
                            assert not status.active.progress_error, status.active.progress_error
                            observed_progress.append(status.active.pipeline)
                        await asyncio.wait({task}, timeout=0.2)
                    await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            status = await coordinator.status()
            assert not status.failed and not status.last_error, status.last_error
            assert coordinator.rebuild.state.phase != "failed", coordinator.rebuild.state.error
            if coordinator.rebuild.state.phase == "ready":
                break
        assert coordinator.rebuild.state.phase == "ready"
        assert all(p.completed_items is None or p.completed_items >= 0 for p in observed_progress)
        assert coordinator.rebuild.state.legacy_preserved == 2
        db = await store._get_db()
        saved = await (await db.execute("SELECT document_json FROM cognee_legacy_documents")).fetchone()
        assert saved is not None and json.loads(saved[0])["text"] == legacy_text
        mapping = await store.get_cognee_mapping(mid)
        assert mapping is not None
        # 真实图库缺失而 SQL 仍标记完成时，验收和旧数据清理都必须失败。
        import subprocess
        import sys

        damage = """
import sys
from pathlib import Path
import cognee_db_workers
from ladybug import Database, Connection
for path in Path(sys.argv[1]).rglob('*.lbug'):
    if '.publication' in path.parts:
        continue
    db = Database(str(path), buffer_pool_size=64 * 1024**2, max_num_threads=2)
    connection = Connection(db)
    connection.execute('MATCH (n:Node) WHERE n.id = $id DETACH DELETE n', {'id': sys.argv[2]})
    connection.close()
    db.close()
"""
        async with client.idle_snapshot():
            damaged = await asyncio.to_thread(subprocess.run,
                [sys.executable, "-c", damage, config.absolute_data_root, mapping["data_id"]],
                capture_output=True, timeout=30)
            assert damaged.returncode == 0, damaged.stderr.decode(errors="replace")
        with pytest.raises(RuntimeError, match="实际图谱缺少"):
            await coordinator.rebuild.verify_backend(coordinator.rebuild.state, await store.projection_state.mappings())
        old_namespace = coordinator.rebuild.state.namespace
        await coordinator.rebuild.request(config, restart=True)
        retired_during_build = False
        for _ in range(8):
            await coordinator.rebuild.advance()
            if coordinator.rebuild.state.phase == "building" and not retired_during_build:
                before_queue = await store.get_cognee_sync_status()
                before_mappings = await store.projection_state.mappings()
                assert before_queue["pending"] and len(before_mappings) == 1
                config.sync_enabled = coordinator.config.sync_enabled = False
                delete = client.delete_dataset

                async def fail_after_first_delete(dataset_id):
                    await delete(dataset_id)
                    raise RuntimeError("injected generation cleanup interruption")

                # 真实 SDK 已删除第一个数据集后失败，必须整轮回滚，不能只剩半轮。
                with monkeypatch.context() as patch:
                    patch.setattr(client, "delete_dataset", fail_after_first_delete)
                    await coordinator.retirement.request_generation("anelf")
                    await coordinator.retirement._task
                assert coordinator.retirement.status.stage == "failed"
                names = {row["name"] for row in await client.list_datasets()}
                assert {"anelf_global", "anelf_relations", old_namespace + "_global"} <= names
                await coordinator.retirement.request_generation("anelf")
                await coordinator.retirement._task
                result = coordinator.retirement.status
                assert result.stage == "completed", result.error
                assert result.deleted_datasets == result.datasets == 2
                assert await store.get_cognee_sync_status() == before_queue
                assert await store.projection_state.mappings() == before_mappings
                assert coordinator.rebuild.state.phase == "building"
                names = {row["name"] for row in await client.list_datasets()}
                assert not {"anelf_global", "anelf_relations"} & names
                assert old_namespace + "_global" in names
                retired_during_build = True
                config.sync_enabled = coordinator.config.sync_enabled = True
            batch = await store.claim_cognee_sync_batch(1)
            if batch:
                await coordinator._process_batch(batch)
            assert coordinator.rebuild.state.phase != "failed", coordinator.rebuild.state.error
            if coordinator.rebuild.state.phase == "ready":
                break
        assert retired_during_build
        assert coordinator.rebuild.state.phase == "ready"
        assert coordinator.rebuild.state.namespace != old_namespace
        mapping = await store.get_cognee_mapping(mid)
        assert mapping is not None and (await store.get(mid)).content.endswith("杭州。")
        dataset_name = mapping["dataset_name"]
        assert (await client.pipeline_progress(mapping["dataset_id"])).status == "DATASET_PROCESSING_COMPLETED"
        # 模拟重新配置/重启后的读取上下文，自动探测维度必须从卷身份恢复。
        detected = client._embedding_payload["embedding_dimensions"]
        client.reconfigure(config)
        assert (await client.initialize()).ready
        assert client._embedding_payload["embedding_dimensions"] == detected
        results = await client.recall(
            "蓝鸢项目在哪里部署？", query_type=client.search_type("CHUNKS"),
            datasets=[dataset_name], node_name=["future:tag"], top_k=5, auto_route=False,
        )
        assert any("杭州" in item.content for item in results)
        projected = await search_cognee(client, config, "蓝鸢项目部署地点", [dataset_name], 5,
                                        ["CHUNKS"], node_names=["future:tag"])
        assert any(item.memory_reference() == f"mem:{mid}" and item.sensitivity == "private"
                   and "future:tag" in item.tags for item in projected), projected
        config.sync_enabled = coordinator.config.sync_enabled = False
        assert all(row["name"] not in {"anelf_global", "anelf_relations"} for row in await client.list_datasets())
        assert len(await client.list_data(mapping["dataset_id"])) == 2
        # 删除旧代际后仍从当前图谱召回，历史正文仍完整保存在原生库。
        saved = await (await db.execute("SELECT document_json FROM cognee_legacy_documents")).fetchall()
        assert all(json.loads(row[0])["text"] == legacy_text for row in saved)
        assert await client.recall("蓝鸢项目", query_type=client.search_type("CHUNKS"),
            datasets=[dataset_name], node_name=["missing:tag"], auto_route=False) == []
        graph = await client.recall("谁负责蓝鸢项目？", query_type=client.search_type("GRAPH_COMPLETION"),
            datasets=[dataset_name], only_context=True, include_references=True, auto_route=False)
        assert any("林舟" in item.content for item in graph), graph
        assert any(item.metadata.get("references") for item in graph)
        await coordinator.improve(dataset_name, review_conflicts=True)
        assert client.global_context_ready(dataset_name)
        entry = await store.get(mid)
        assert entry is not None
        entry.tags.append("stage:updated")
        entry.content = "林舟负责蓝鸢项目。蓝鸢项目的部署地点已改为苏州。"
        await store.update(entry)
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        status = await coordinator.status()
        assert not status.failed and not status.last_error, status.last_error
        results = await client.recall("蓝鸢项目部署地点", query_type=client.search_type("CHUNKS"),
            datasets=[dataset_name], node_name=["stage:updated"], auto_route=False)
        assert any("苏州" in item.content for item in results)
        assert len(await client.list_data(mapping["dataset_id"])) == 2
        assert not client.global_context_ready(dataset_name)
        graph = await client.recall("蓝鸢项目部署地点", query_type=client.search_type("GRAPH_COMPLETION"),
            datasets=[dataset_name], only_context=True, include_references=True, auto_route=False)
        assert not any("杭州" in item.content for item in graph), graph
        assert any("苏州" in item.content for item in graph), graph
        await coordinator.improve(dataset_name)
        overview = await search_cognee(client, config, "概括蓝鸢项目负责人和当前部署地点", [dataset_name], 5,
                                       ["GRAPH_COMPLETION"], overview=True)
        assert any("苏州" in item.snippet for item in overview), overview
        await store.delete(mid)
        await coordinator._process_batch(await store.claim_cognee_sync_batch(10))
        assert not client.global_context_ready(dataset_name)
        remaining = await client.list_data(mapping["dataset_id"])
        assert len(remaining) == 1
        surviving = await client.recall("白鹭项目使用什么语言", query_type=client.search_type("CHUNKS"),
            datasets=[dataset_name], node_name=["project:白鹭"], auto_route=False)
        assert any("Rust" in item.content for item in surviving), surviving
        graph = await client.recall("蓝鸢项目", query_type=client.search_type("GRAPH_COMPLETION"),
            datasets=[dataset_name], only_context=True, include_references=True, auto_route=False)
        assert not any("蓝鸢" in item.content or "苏州" in item.content for item in graph), graph
        assert await store.get(shared) is not None
        assert await store.get_cognee_mapping(mid) is None
    finally:
        await coordinator.close()
        await store.close()
