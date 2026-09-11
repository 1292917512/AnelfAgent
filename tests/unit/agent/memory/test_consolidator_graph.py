"""图谱治理单元测试：访问追踪 / 强度松弛护盾 / 弱边遗忘 / 孤立节点归档。"""

from __future__ import annotations

import math
import time

import pytest


async def _backdate_edge(store, edge_id: int, days: float) -> None:
    """把边的时间戳拨回 N 天前（治理阈值的测试前提）。"""
    db = await store._get_db()
    cutoff_ns = time.time_ns() - int(days * 86_400 * 1e9)
    await db.execute(
        "UPDATE graph_edges SET updated_ns=?, last_accessed_ns=0 WHERE id=?",
        (cutoff_ns, edge_id),
    )
    await db.commit()


async def _backdate_node(store, node_id: int, days: float) -> None:
    db = await store._get_db()
    cutoff_ns = time.time_ns() - int(days * 86_400 * 1e9)
    await db.execute(
        "UPDATE graph_nodes SET updated_ns=? WHERE id=?", (cutoff_ns, node_id),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_edges_for_scopes_records_access(store) -> None:
    """检索命中即计数：access_count 递增 + last_accessed 刷新。"""
    await store.graph.add_relation("user:qq:1", "朋友", "user:qq:2")
    edges = await store.graph.edges_for_scopes(["user:qq:1"])
    assert len(edges) == 1
    assert edges[0]["access_count"] == 0  # 返回的是查询前快照
    edges2 = await store.graph.edges_for_scopes(["user:qq:1"])
    assert edges2[0]["access_count"] == 1
    assert edges2[0]["last_accessed"] > 0


@pytest.mark.asyncio
async def test_relax_edge_strength_with_access_shield(store) -> None:
    """松弛向基线回归；高访问计数护盾使回归更慢（与记忆松弛同公式）。"""
    shielded = await store.graph.add_relation(
        "user:qq:1", "朋友", "user:qq:2", strength=0.9,
    )
    plain = await store.graph.add_relation(
        "user:qq:1", "同事", "user:qq:3", strength=0.9,
    )
    db = await store._get_db()
    await db.execute("UPDATE graph_edges SET access_count=10 WHERE id=?", (shielded["id"],))
    await db.commit()
    await _backdate_edge(store, shielded["id"], 40)
    await _backdate_edge(store, plain["id"], 40)

    relaxed = await store.graph.relax_edge_strength(stale_days=30, rate=0.05)
    assert relaxed == 2

    s_after = (await store.graph.get_relation(shielded["id"]))["strength"]
    p_after = (await store.graph.get_relation(plain["id"]))["strength"]
    shield = 1.0 + math.log(10)
    assert p_after == pytest.approx(0.5 + 0.4 * (1 - 0.05), abs=0.01)
    assert s_after == pytest.approx(0.5 + 0.4 * (1 - 0.05 / shield), abs=0.01)
    assert s_after > p_after
    # 均未跌穿基线
    assert s_after > 0.5 and p_after > 0.5


@pytest.mark.asyncio
async def test_relax_skips_recent_edges(store) -> None:
    await store.graph.add_relation("user:qq:1", "朋友", "user:qq:2", strength=0.9)
    assert await store.graph.relax_edge_strength(stale_days=30, rate=0.05) == 0


@pytest.mark.asyncio
async def test_forget_weak_edges_archives_stale_weak(store) -> None:
    weak = await store.graph.add_relation(
        "user:qq:1", "认识", "user:qq:2", strength=0.2,
    )
    strong = await store.graph.add_relation(
        "user:qq:1", "朋友", "user:qq:3", strength=0.9,
    )
    await _backdate_edge(store, weak["id"], 100)
    await _backdate_edge(store, strong["id"], 100)

    archived = await store.graph.forget_weak_edges(
        min_age_days=90, strength_threshold=0.25,
    )
    assert archived == 1
    assert (await store.graph.get_relation(weak["id"]))["archived"] is True
    assert (await store.graph.get_relation(strong["id"]))["archived"] is False


@pytest.mark.asyncio
async def test_forget_weak_edges_respects_min_age(store) -> None:
    """弱但未到最小年龄的边不归档（阈值保守，缓回收）。"""
    weak = await store.graph.add_relation(
        "user:qq:1", "认识", "user:qq:2", strength=0.2,
    )
    await _backdate_edge(store, weak["id"], 30)
    assert await store.graph.forget_weak_edges(
        min_age_days=90, strength_threshold=0.25,
    ) == 0
    assert (await store.graph.get_relation(weak["id"]))["archived"] is False


@pytest.mark.asyncio
async def test_archive_orphan_nodes_skips_session_anchors(store) -> None:
    """孤立自由型节点归档；user/group 会话锚点永不自动归档。"""
    user_edge = await store.graph.add_relation("user:qq:1", "朋友", "user:qq:2")
    orphan_topic = await store.graph.upsert_node("topic:旧项目", label="旧项目")
    anchor_user = await store.graph.upsert_node("user:qq:9", label="潜水用户")
    # topic 节点带边时不归档 → 先建边再删边制造孤立
    tmp_edge = await store.graph.add_relation(
        "topic:旧项目", "关联", "topic:另一话题", strength=0.9,
    )
    await store.graph.set_relation_archived(tmp_edge["id"], True)
    # 保留一条 user 边（user:qq:1 与 user:qq:2 互为邻居）；user:qq:9 孤立
    assert user_edge is not None

    await _backdate_node(store, orphan_topic["id"], 70)
    await _backdate_node(store, anchor_user["id"], 70)

    archived = await store.graph.archive_orphan_nodes(min_age_days=60)
    assert archived == 1
    assert (await store.graph.get_node("topic:旧项目"))["archived"] is True
    assert (await store.graph.get_node("user:qq:9"))["archived"] is False


@pytest.mark.asyncio
async def test_governance_runs_in_consolidator(store) -> None:
    """整理器步骤接线：图谱治理在 consolidate() 内执行并产出报告字段。"""
    from agent.memory.consolidator import MemoryConsolidator

    weak = await store.graph.add_relation(
        "user:qq:1", "认识", "user:qq:2", strength=0.2,
    )
    await _backdate_edge(store, weak["id"], 100)

    report = await MemoryConsolidator(store).consolidate()
    assert report.graph_relaxed >= 0
    assert report.graph_edges_forgotten == 1
    assert "图谱治理" in " ".join(report.to_log_lines())
