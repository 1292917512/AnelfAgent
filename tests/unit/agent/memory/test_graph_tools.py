"""graph 工具组单元测试：注册、CRUD、归一化与错误返回。"""

from __future__ import annotations

import json

import pytest

from agent.memory.graph import tools as graph_tools
from agent.memory.memory_store import MemoryStore


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    graph_tools.register_graph_tools(s)
    yield s
    await s.close()
    graph_tools._store = None


@pytest.mark.asyncio
async def test_register_activates_group(store) -> None:
    from core.entity import EntityRegistry
    names = {name for name, e in EntityRegistry._entities.items() if e.group == "graph"}
    assert {
        "graph_add_relation", "graph_update_relation", "graph_remove_relation",
        "graph_upsert_node", "graph_remove_node", "graph_merge_nodes",
        "graph_query", "graph_path", "graph_list_nodes",
    } <= names


@pytest.mark.asyncio
async def test_add_and_query_relation_roundtrip(store) -> None:
    raw = await graph_tools.graph_add_relation(
        "user:qq:1", "朋友", "person:老王",
        object_label="老王", symmetric=True, strength=0.9, evidence="常一起吃饭",
    )
    data = json.loads(raw)
    assert data["ok"] is True
    assert "user:qq:1" in data["edge"]["triple"]

    raw = await graph_tools.graph_query("person:老王")
    data = json.loads(raw)
    assert data["found"] is True
    assert data["edge_count"] == 1
    assert "朋友│0.90" in data["edges"][0]["triple"]


@pytest.mark.asyncio
async def test_add_relation_param_error(store) -> None:
    raw = await graph_tools.graph_add_relation("user:qq:1", "", "user:qq:2")
    data = json.loads(raw)
    assert "error" in data
    assert data["cause"] == "param"


@pytest.mark.asyncio
async def test_update_and_remove_relation(store) -> None:
    raw = await graph_tools.graph_add_relation("user:qq:1", "同事", "user:qq:2")
    edge_id = json.loads(raw)["edge"]["id"]

    raw = await graph_tools.graph_update_relation(edge_id, predicate="好友", strength=0.95)
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["edge"]["predicate"] == "好友"

    raw = await graph_tools.graph_remove_relation(edge_id)
    assert json.loads(raw)["ok"] is True
    raw = await graph_tools.graph_query("user:qq:1")
    assert json.loads(raw)["edge_count"] == 0

    # 移除不存在的边 → NOT_FOUND
    raw = await graph_tools.graph_remove_relation(edge_id)
    assert "error" in json.loads(raw)


@pytest.mark.asyncio
async def test_node_tools(store) -> None:
    raw = await graph_tools.graph_upsert_node("person:老王", label="王师傅", metadata='{"城市": "上海"}')
    assert json.loads(raw)["ok"] is True

    raw = await graph_tools.graph_list_nodes(node_type="person")
    data = json.loads(raw)
    assert data["total_nodes"] == 1
    assert data["nodes"][0]["label"] == "王师傅"

    raw = await graph_tools.graph_upsert_node("person:老王", metadata="不是json")
    assert "error" in json.loads(raw)

    raw = await graph_tools.graph_remove_node("person:老王")
    assert json.loads(raw)["ok"] is True
    raw = await graph_tools.graph_list_nodes(node_type="person")
    assert json.loads(raw)["total_nodes"] == 0


@pytest.mark.asyncio
async def test_merge_and_path(store) -> None:
    await graph_tools.graph_add_relation("person:老王", "同事", "user:qq:2")
    raw = await graph_tools.graph_merge_nodes("person:老王", "user:qq:9")
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["edges_moved"] == 1

    raw = await graph_tools.graph_path("user:qq:9", "user:qq:2")
    data = json.loads(raw)
    assert data["found"] is True
    assert data["hops"] == 1
