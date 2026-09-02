"""GraphStore 单元测试：关系图谱权威存储的 CRUD / 查询 / 合并 / 投影入队。"""

from __future__ import annotations

import pytest

from agent.memory.graph import format_triple, parse_node_key

# ==================================================================
# node_key 解析
# ==================================================================


def test_parse_node_key_builtin_types() -> None:
    assert parse_node_key("user:qq:123") == ("user", "qq:123")
    assert parse_node_key("person:老王") == ("person", "老王")
    assert parse_node_key("topic:火锅") == ("topic", "火锅")


def test_parse_node_key_unknown_prefix_becomes_custom() -> None:
    assert parse_node_key("galaxy:地球")[0] == "custom"


def test_parse_node_key_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        parse_node_key("没有冒号")


# ==================================================================
# 关系 CRUD
# ==================================================================


@pytest.mark.asyncio
async def test_add_relation_auto_creates_nodes(store) -> None:
    edge = await store.graph.add_relation(
        "user:qq:123", "朋友", "person:老王",
        subject_label="阿辰", object_label="老王",
        symmetric=True, strength=0.9, evidence="经常一起吃饭",
    )
    assert edge["predicate"] == "朋友"
    assert edge["symmetric"] is True
    assert edge["subject"]["label"] == "阿辰"
    assert edge["object"]["node_type"] == "person"
    assert "阿辰(user:qq:123)" in format_triple(edge)


@pytest.mark.asyncio
async def test_add_relation_upserts_on_same_triple(store) -> None:
    first = await store.graph.add_relation("user:qq:1", "同事", "user:qq:2", strength=0.5)
    second = await store.graph.add_relation(
        "user:qq:1", "同事", "user:qq:2", strength=0.9, evidence="同组",
    )
    assert second["id"] == first["id"]
    assert second["strength"] == 0.9
    assert second["evidence"] == "同组"
    stats = await store.graph.stats()
    assert stats["edges"] == 1


@pytest.mark.asyncio
async def test_add_relation_rejects_self_loop_and_empty_predicate(store) -> None:
    with pytest.raises(ValueError):
        await store.graph.add_relation("user:qq:1", "认识", "user:qq:1")
    with pytest.raises(ValueError):
        await store.graph.add_relation("user:qq:1", "  ", "user:qq:2")


@pytest.mark.asyncio
async def test_alias_resolver_normalizes_entity_keys(store) -> None:
    async def resolver(scope_type: str, scope_id: str):
        if (scope_type, scope_id) == ("user", "webui:web_user"):
            return ("user", "qq:123")
        return None

    store.graph.set_alias_resolver(resolver)
    edge = await store.graph.add_relation("user:webui:web_user", "喜欢", "topic:火锅")
    assert edge["subject"]["node_key"] == "user:qq:123"
    # 查询侧同样归一
    result = await store.graph.query_relations("user:webui:web_user")
    assert result["found"] is True
    assert result["node"]["node_key"] == "user:qq:123"


@pytest.mark.asyncio
async def test_update_and_archive_relation(store) -> None:
    edge = await store.graph.add_relation("user:qq:1", "同事", "user:qq:2")
    updated = await store.graph.update_relation(
        edge["id"], predicate="好友", strength=0.8, symmetric=True,
    )
    assert updated is not None
    assert updated["predicate"] == "好友"
    assert updated["symmetric"] is True

    assert await store.graph.set_relation_archived(edge["id"], True) is True
    result = await store.graph.query_relations("user:qq:1")
    assert result["edges"] == []
    assert await store.graph.set_relation_archived(edge["id"], False) is True
    result = await store.graph.query_relations("user:qq:1")
    assert len(result["edges"]) == 1


@pytest.mark.asyncio
async def test_node_archive_cascades_edges(store) -> None:
    await store.graph.add_relation("user:qq:1", "同事", "user:qq:2")
    assert await store.graph.set_node_archived("user:qq:2", True) is True
    result = await store.graph.query_relations("user:qq:1")
    assert result["edges"] == []
    # 恢复节点不级联恢复边（语义文档化）
    assert await store.graph.set_node_archived("user:qq:2", False) is True
    result = await store.graph.query_relations("user:qq:1")
    assert result["edges"] == []


# ==================================================================
# 查询与路径
# ==================================================================


@pytest.mark.asyncio
async def test_query_relations_depth_two(store) -> None:
    await store.graph.add_relation("user:a:1", "认识", "user:a:2")
    await store.graph.add_relation("user:a:2", "认识", "user:a:3")
    shallow = await store.graph.query_relations("user:a:1", depth=1)
    assert len(shallow["edges"]) == 1
    deep = await store.graph.query_relations("user:a:1", depth=2)
    assert len(deep["edges"]) == 2
    assert {n["node_key"] for n in deep["nodes"]} == {"user:a:1", "user:a:2", "user:a:3"}


@pytest.mark.asyncio
async def test_find_path_bfs(store) -> None:
    await store.graph.add_relation("user:a:1", "认识", "user:a:2")
    await store.graph.add_relation("user:a:2", "同事", "user:a:3")
    await store.graph.add_relation("user:a:1", "直达", "user:a:3")
    path = await store.graph.find_path("user:a:1", "user:a:3")
    assert len(path) == 1
    assert path[0]["predicate"] == "直达"
    # 反向遍历（无向）
    path = await store.graph.find_path("user:a:3", "user:a:2", max_depth=1)
    assert len(path) == 1
    assert await store.graph.find_path("user:a:1", "user:b:9") == []


# ==================================================================
# 合并
# ==================================================================


@pytest.mark.asyncio
async def test_merge_nodes_rewires_and_merges_conflicts(store) -> None:
    await store.graph.add_relation("person:老王", "同事", "user:qq:2", strength=0.6)
    await store.graph.add_relation("user:qq:9", "同事", "user:qq:2", strength=0.8, evidence="实锤")
    await store.graph.add_relation("person:老王", "喜欢", "topic:羽毛球")

    report = await store.graph.merge_nodes("person:老王", "user:qq:9")
    assert report["edges_moved"] == 1  # 喜欢→羽毛球
    assert report["edges_merged"] == 1  # 同事冲突合并
    assert report["edges_dropped"] == 0

    source = await store.graph.get_node("person:老王")
    assert source is not None and source["archived"] is True
    assert source["metadata"]["merged_into"] == "user:qq:9"

    result = await store.graph.query_relations("user:qq:9")
    assert len(result["edges"]) == 2
    colleague = next(e for e in result["edges"] if e["predicate"] == "同事")
    assert colleague["strength"] == 0.8


# ==================================================================
# cognee 投影入队
# ==================================================================


@pytest.mark.asyncio
async def test_relation_change_enqueues_graph_node_projection(store) -> None:
    store.set_cognee_projection_enabled(True)
    await store.graph.add_relation("user:qq:1", "朋友", "user:qq:2")
    batch = await store.claim_cognee_sync_batch(10)
    kinds = {(item["entry_kind"], item["entry_id"]) for item in batch}
    assert len(batch) == 2  # 两端节点各一条
    assert all(kind == "graph_node" for kind, _ in kinds)
    assert all(item["operation"] == "upsert" for item in batch)


@pytest.mark.asyncio
async def test_render_node_document(store) -> None:
    edge = await store.graph.add_relation(
        "user:qq:1", "喜欢", "topic:火锅",
        subject_label="阿辰", strength=0.8, evidence="每周必吃",
    )
    doc = await store.graph.render_node_document(edge["subject"]["id"])
    assert doc is not None
    assert "[关系节点] 阿辰（user:qq:1" in doc
    assert "喜欢 → 火锅（topic:火锅）" in doc
    assert "每周必吃" in doc
    # 归档节点不渲染
    await store.graph.set_node_archived("user:qq:1", True)
    assert await store.graph.render_node_document(edge["subject"]["id"]) is None


@pytest.mark.asyncio
async def test_render_node_projection_fingerprint_ignores_strength_and_evidence(store) -> None:
    edge = await store.graph.add_relation(
        "user:qq:1", "喜欢", "topic:火锅", strength=0.5, evidence="初识",
    )
    node_id = edge["subject"]["id"]
    first = await store.graph.render_node_projection(node_id)
    assert first is not None
    doc_a, fp_a = first
    # 强度强化与证据刷新：文档文本变化但结构指纹不变（投影跳过的依据）
    await store.graph.add_relation(
        "user:qq:1", "喜欢", "topic:火锅", strength=0.9, evidence="每周必吃",
    )
    second = await store.graph.render_node_projection(node_id)
    assert second is not None
    doc_b, fp_b = second
    assert fp_a == fp_b
    assert doc_a != doc_b
    # 邻域结构变化（新增边）：指纹必须变化以触发重投影
    await store.graph.add_relation("user:qq:1", "讨厌", "topic:香菜")
    third = await store.graph.render_node_projection(node_id)
    assert third is not None
    assert third[1] != fp_a
    # 归档节点不渲染
    await store.graph.set_node_archived("user:qq:1", True)
    assert await store.graph.render_node_projection(node_id) is None
