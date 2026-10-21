from __future__ import annotations

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
async def test_real_projection_rebuild_tagged_recall_update_delete(tmp_path, monkeypatch) -> None:
    """使用本地已配置模型、真实 Cognee 数据库和项目投影链，所有数据写入临时目录。"""
    config = load_cognee_config()
    config.enabled = config.sync_enabled = True
    config.project_memories_enabled = config.project_graph_enabled = True
    config.data_root = str(tmp_path / "cognee")
    config.timeout_seconds, config.pipeline_timeout_seconds = 120, 600
    config.write_breaker_enabled = False
    monkeypatch.setattr("agent.memory.cognee.rebuild.backups_root", lambda: tmp_path / "backups")
    client = CogneeClient(config)
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
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
        await coordinator.rebuild.request(config)
        for _ in range(8):
            await coordinator.rebuild.advance()
            batch = await store.claim_cognee_sync_batch(10)
            if batch:
                await coordinator._process_batch(batch)
            status = await coordinator.status()
            assert not status.failed and not status.last_error, status.last_error
            assert coordinator.rebuild.state.phase != "failed", coordinator.rebuild.state.error
            if coordinator.rebuild.state.phase == "ready":
                break
        assert coordinator.rebuild.state.phase == "ready"
        assert coordinator.rebuild.state.legacy_preserved == 1
        db = await store._get_db()
        saved = await (await db.execute("SELECT document_json FROM cognee_legacy_documents")).fetchone()
        assert saved is not None and json.loads(saved[0])["text"] == legacy_text
        mapping = await store.get_cognee_mapping(mid)
        assert mapping is not None
        dataset_name = mapping["dataset_name"]
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
