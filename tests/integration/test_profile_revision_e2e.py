"""真实配置模型验收：规则 → 工具调用 → 画像提交 → 镜像/向量 → 自主晋升。

PROFILE_REVISION_E2E=1 uv run pytest tests/integration/test_profile_revision_e2e.py -s
使用本地模型与规则的临时副本；数据库、配置写入和运行账本仅落测试目录。
模型与工具实现均不替换，消费真实 token；默认跳过。
"""

from __future__ import annotations

import json
import os
import shutil
from types import SimpleNamespace

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("PROFILE_REVISION_E2E") != "1", reason="set PROFILE_REVISION_E2E=1 with configured models"),
    pytest.mark.timeout(600),
]


@pytest.fixture
async def live_profiles(tmp_path, monkeypatch):
    from agent.llm import llm_manager
    from agent.memory import profile_tools, tools
    from agent.memory.embedding import Embedder
    from agent.memory.memory_store import MemoryStore
    from agent.memory.profiles import ProfileWriter
    from agent.storage.data_center import EverythingData
    from agent.storage.sqlite_backend import SqliteBackend
    from agent.storage.storage_router import StorageRouter
    from core.config import ConfigManager
    from core.path import ConfigPaths

    assert profile_tools.profile_history is not None
    for name, source in {"app_config.json": ConfigPaths.APP_CONFIG, "llm_clients.json": ConfigPaths.LLM_CLIENTS,
                         "memory_rules.md": ConfigPaths.MEMORY_RULES}.items():
        shutil.copyfile(source, tmp_path / name)
    monkeypatch.setenv("ANELF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(ConfigPaths, "MEMORY_RULES", str(tmp_path / "memory_rules.md"))
    ConfigManager.initialize(str(tmp_path / "app_config.json"))
    ConfigManager.set("workspace_root", str(tmp_path / "workspace"))
    manager = llm_manager.LLMManager(str(tmp_path / "llm_clients.json"))
    monkeypatch.setattr(llm_manager, "_manager", manager)
    backend = SqliteBackend(str(tmp_path / "agent.sqlite3"))
    store = MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.set_cognee_projection_enabled(False)
    entities = EverythingData(StorageRouter(backend))
    runtime = SimpleNamespace(data_center=SimpleNamespace(sqlite=backend, everything_data=entities), memory_store=store)
    monkeypatch.setattr("agent.runtime.singleton.require_runtime", lambda: runtime)
    embedder = Embedder()
    tools.memory_tools_port.set(tools.MemoryToolDeps(store, embedder))
    try:
        yield ProfileWriter(entities, store), manager, embedder
    finally:
        tools.memory_tools_port.unbind()
        await store.close()
        await backend.close()
        await manager.close()


async def test_real_ai_revision_obeys_effective_rules_and_preserves_identity(live_profiles):
    from agent.context.assembly import _memory_rules_text
    from agent.memory.memory_types import MemoryEntry, MemoryType
    from core.entity import EntityRegistry
    from core.tool_registry import activate_group
    from entities.entity_query import tools as config_tools

    writer, manager, embedder = live_profiles
    assert config_tools.get_entity_config is not None
    rules = _memory_rules_text()
    assert "只能由反思晋升写入" not in rules and "无需人工审核" in rules
    assert "无需等待人工确认" in rules
    old = "我优先使用中文交流。我习惯未核实就先执行再解释。"
    await writer.sqlite.set_entity_personality(scope_type="agent", scope_id="self", personality=old, conv_num=1200, conv_update_num=17)
    mid = await writer.store.add(MemoryEntry(memory_type=MemoryType.ENTITY, content=old,
        source="entity_agent_self", tags=["agent:self", "topic:旧标签"], metadata={"legacy": "preserve"}))
    baseline = await writer.read("agent", "self")
    activate_group("memory")
    allowed = ["get_entity_profile", "update_entity_profile", "profile_history", "sync_profiles", "get_entity_config"]
    schemas = EntityRegistry.get_tool_schema_by_names(allowed)
    assert len(schemas) == len(allowed)
    client = manager.get_chat_client(require_tools=True)
    assert client is not None
    messages = [
        {"role": "system", "content": rules},
        {"role": "user", "content": "已核实的更正：你应先核实事实再行动，旧画像中未核实就执行的描述已不适用。请自主修正 agent:self，自画像中的中文优先保持不变；随后查看本次修订历史与同步状态，并读取 memory/profile_history 保留配置。请实际调用工具完成。"},
    ]
    calls: list[str] = []
    for _ in range(7):
        response = await client.chat(messages, tools=schemas, options={"max_tokens": 1600, "temperature": 0, "parallel_tool_calls": False})
        if not response.tool_calls:
            break
        messages.append({"role": "assistant", "content": response.content or "", "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
            for call in response.tool_calls
        ]})
        for call in response.tool_calls:
            assert call.name in allowed
            result = await EntityRegistry.execute_tool(call.name, call.arguments)
            assert not json.loads(result).get("error"), result
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
            calls.append(call.name)
    assert {"get_entity_profile", "update_entity_profile", "profile_history", "get_entity_config"} <= set(calls), calls
    current = await writer.read("agent", "self")
    assert current["revision"] != baseline["revision"] and "中文" in current["personality"] and "核实" in current["personality"]
    assert (current["conv_num"], current["conv_update_num"]) == (1200, 17)
    mirror = await writer.store.get(mid)
    assert mirror.content == current["personality"] and mirror.metadata == {"legacy": "preserve"}
    assert mirror.tags == ["agent:self", "topic:旧标签"]
    operations = await writer.operations.timeline()
    assert any(op.actor == "tool:entity_profile" and op.synced and op.reason for op in operations)
    assert await writer.store.backfill_embeddings(embedder) == 1
    vector = await embedder.embed_query("我行动前如何核实事实？")
    assert vector
    recalled = await writer.store.search_vector(vector, limit=3, min_score=0)
    assert [entry.id for entry, _ in recalled] == [mid]
    assert recalled[0][0].content == current["personality"]
    print("REAL PROFILE FLOW:", client.config.model, calls, "same memory ID, counters, tags and metadata preserved; embedding recall passed")


async def test_real_promotion_selects_subject_not_first_participant(live_profiles):
    from agent.memory import evidence
    from agent.memory.memory_types import MemoryEntry, MemoryType
    from agent.memory.reflection_lifecycle import advance_reflections, seed_reflection

    writer, _, _ = live_profiles
    for person in ["observer", "subject"]:
        await writer.sqlite.set_entity_personality(scope_type="user", scope_id=f"qq:{person}", personality=f"{person} 的既有认知", conv_num=90)
    entry = MemoryEntry(memory_type=MemoryType.REFLECTION,
        content="已多次由本人确认：user:qq:subject 长期偏好无糖茶。user:qq:observer 只是本次对话的参与者，没有证据说明 observer 的饮食偏好。",
        tags=["type:reflection", "user:qq:observer", "user:qq:subject"])
    seed_reflection(entry)
    evidence.apply_reinforcement(entry.metadata, 4, user_originated=True)
    entry.metadata["lifecycle"]["status"] = "confirmed"
    mid = await writer.store.add(entry)
    report = await advance_reflections(writer.store, writer)
    assert report.promoted == 1, report
    saved = await writer.store.get(mid)
    assert saved.metadata["lifecycle"]["absorbed_into"] == "profile:user:qq:subject"
    assert (await writer.read("user", "qq:observer"))["personality"] == "observer 的既有认知"
    subject = await writer.read("user", "qq:subject")
    assert "无糖茶" in subject["personality"] and subject["conv_num"] == 90
    before = len(await writer.operations.timeline())
    assert (await advance_reflections(writer.store, writer)).promoted == 0
    assert len(await writer.operations.timeline()) == before
    print("REAL PROMOTION FLOW: subject selected from multiple participant tags; duplicate promotion prevented")
