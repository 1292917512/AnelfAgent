"""标签智能（TagIntelligence）单元测试：共现联想 / 查询提及识别 / 种子三层扩展。"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.store.tag_intel import TagIntelligence


async def test_ambiguous_labels_do_not_choose_an_identity(store) -> None:
    await store.graph.add_relation(
        "user:qq:100", "同事", "user:webui:200",
        subject_label="小明", object_label="小明",
    )
    assert await store.extract_query_mentions("小明说的方案") == []
    await store.add(MemoryEntry(
        memory_type=MemoryType.SEMANTIC, content="QQ 的小明的方案", tags=["user:qq:100"],
    ))
    results = await store.search_by_tags(["user:qq:100"])
    assert [entry.content for entry in results] == ["QQ 的小明的方案"]


async def test_latin_mentions_match_words_and_mixed_chinese_text(store) -> None:
    for content in ("NAS 状态", "NAS 使用方式"):
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content=content, tags=["topic:NAS"],
        ))
    assert await store.extract_query_mentions("nasal anatomy") == []
    assert await store.extract_query_mentions("my_nas_backup") == []
    assert await store.extract_query_mentions("检查NAS，nas是否在线") == ["topic:NAS"]
    assert await store.extract_query_mentions("NAS", limit=0) == []


async def test_tag_document_frequency_counts_documents(store) -> None:
    intel = TagIntelligence(store._conn)
    rows = [json.dumps(["topic:NAS", "topic:NAS", "topic:备份"])]
    df, cooc, vocab = intel._compute(rows, [])
    assert df == {"topic:NAS": 1, "topic:备份": 1}
    assert list(cooc.values()) == [1]
    assert vocab == {}


async def test_fresh_statistics_reuse_unchanged_snapshot_and_track_writes(store, monkeypatch) -> None:
    mid = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content="事实", tags=["topic:old"]))
    assert await store.list_tags(fresh=True) == {"topic:old": 1}
    intel = store._search._tag_intel
    original = intel._compute
    calls = 0

    def counted(tag_rows, node_rows):
        nonlocal calls
        calls += 1
        return original(tag_rows, node_rows)

    monkeypatch.setattr(intel, "_compute", counted)
    assert await store.list_tags(fresh=True) == {"topic:old": 1}
    assert calls == 0
    entry = await store.get(mid)
    assert entry is not None
    entry.tags = ["topic:new"]
    await store.update(entry)
    assert await store.list_tags(fresh=True) == {"topic:new": 1}
    await store.update_importance(mid, 0)
    assert await store.list_tags(fresh=True) == {}
    assert calls == 2


async def test_fresh_statistics_wait_for_rollback(store) -> None:
    mid = await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC, content="事实", tags=["topic:kept"]))
    db = await store._conn.get_db()
    started = asyncio.Event()

    async def read():
        started.set()
        return await store.list_tags(fresh=True)

    with pytest.raises(RuntimeError, match="rollback"):
        async with store._conn.tx(db):
            await db.execute("UPDATE memories SET tags_json='[]' WHERE id=?", (mid,))
            reader = asyncio.create_task(read())
            await started.wait()
            raise RuntimeError("rollback")
    assert await asyncio.wait_for(reader, timeout=5) == {"topic:kept": 1}


async def test_tag_cache_ignores_content_and_projection_changes_even_after_ttl(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mid = await store.add(MemoryEntry(content="original", tags=["topic:kept"], memory_type=MemoryType.SEMANTIC))
    assert await store.list_tags(fresh=True) == {"topic:kept": 1}
    intel = store._search._tag_intel
    original = intel._compute
    calls = 0

    def counted(
        tag_rows: list[str], node_rows: list[tuple[str, str]],
    ) -> tuple[dict[str, int], dict[tuple[str, str], int], dict[str, str]]:
        nonlocal calls
        calls += 1
        return original(tag_rows, node_rows)

    monkeypatch.setattr(intel, "_compute", counted)
    for i in range(3):
        entry = await store.get(mid)
        assert entry is not None
        entry.content = f"revision {i}"
        entry.importance = 0.8
        assert await store.update(entry)
        intel._built_at = 0
        assert await store.list_tags(fresh=True) == {"topic:kept": 1}
    assert calls == 0
    await store.graph.add_relation("topic:kept", "related", "topic:other", subject_label="Known label")
    await store.list_tags(fresh=True)
    assert calls == 1
    assert await store.extract_query_mentions("Known label") == ["topic:kept"]


async def test_tag_revision_migrates_existing_database_and_tracks_active_sources(store: MemoryStore) -> None:
    mid = await store.add(MemoryEntry(content="fact", tags=["topic:kept"], memory_type=MemoryType.SEMANTIC))
    db = await store._conn.get_db()
    async with store._conn.tx(db):
        for table in ("memories", "graph_nodes"):
            for operation in ("insert", "update", "delete"):
                await db.execute(f"DROP TRIGGER tag_revision_{table}_{operation}")
        await db.execute("DELETE FROM schema_meta WHERE key='tag_revision'")
    await store.close()
    assert await store.list_tags(fresh=True) == {"topic:kept": 1}
    await store.update_importance(mid, 0)
    assert await store.list_tags(fresh=True) == {}
    await store.update_importance(mid, 0.5)
    assert await store.list_tags(fresh=True) == {"topic:kept": 1}
    await store.delete(mid)
    assert await store.list_tags(fresh=True) == {}

    await store.graph.upsert_node("topic:server", label="Old label")
    await store.list_tags(fresh=True)
    assert await store.extract_query_mentions("Old label") == ["topic:server"]
    await store.graph.upsert_node("topic:server", label="New label")
    await store.list_tags(fresh=True)
    assert await store.extract_query_mentions("Old label") == []
    assert await store.extract_query_mentions("New label") == ["topic:server"]
    await store.graph.set_node_archived("topic:server", True)
    await store.list_tags(fresh=True)
    assert await store.extract_query_mentions("New label") == []
    await store.graph.set_node_archived("topic:server", False)
    await store.list_tags(fresh=True)
    assert await store.extract_query_mentions("New label") == ["topic:server"]


async def _seed(store: MemoryStore) -> None:
    """构造共现场景：火锅×聚餐 高频共现；主人—苗苗 图谱边。"""
    for i in range(3):
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content=f"火锅聚餐记录{i}",
            tags=["topic:火锅", "topic:聚餐"], importance=0.6,
        ))
    await store.add(MemoryEntry(
        memory_type=MemoryType.SEMANTIC, content="只提聚餐的事",
        tags=["topic:聚餐"], importance=0.6,
    ))
    # 高频公共标签（IDF 应压制它的共现排名）
    for i in range(8):
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content=f"日常{i}",
            tags=["user:qq:hot", "topic:火锅"], importance=0.6,
        ))
    await store.graph.add_relation(
        "user:qq:1292917512", "恋人", "user:qq:2997913632",
        subject_label="主人", object_label="苗苗姐姐", symmetric=True,
    )


@pytest.mark.asyncio
async def test_cooccurring_tags(store) -> None:
    await _seed(store)
    cooc = await store.cooccurring_tags(["topic:火锅"], limit=5)
    tags = [t for t, _ in cooc]
    # topic:聚餐 与火锅共现 3 次，应被联想出来
    assert "topic:聚餐" in tags
    # 高频 user:qq:hot 虽共现 8 次，但 IDF 低——排名应在 聚餐 之后或被压出
    if "user:qq:hot" in tags:
        assert tags.index("topic:聚餐") < tags.index("user:qq:hot")


@pytest.mark.asyncio
async def test_extract_mentions(store) -> None:
    await _seed(store)
    # topic 词表命中
    hits = await store.extract_query_mentions("周末去吃火锅怎么样")
    assert "topic:火锅" in hits
    # 图谱节点 label 命中 → 映射为实体标签
    hits = await store.extract_query_mentions("苗苗姐姐喜欢吃什么")
    assert "user:qq:2997913632" in hits
    # 无提及 → 空
    assert await store.extract_query_mentions("zzzz 无关内容") == []


@pytest.mark.asyncio
async def test_expand_tag_seeds_graph_neighbor(store) -> None:
    await _seed(store)
    seeds = await store.expand_tag_seeds(["user:qq:1292917512"])
    # 图谱邻居：恋人边另一端进入种子
    assert "user:qq:2997913632" in seeds


@pytest.mark.asyncio
async def test_associative_recalls_via_cooc(store) -> None:
    """端到端：搜火锅 → 共现扩展出 聚餐 标签 → 只带聚餐标签的记忆被联想召回。"""
    await _seed(store)
    expanded = await store.expand_tag_seeds(["topic:火锅"])
    # 契约：未扩展时它根本不在候选池；扩展后进入联想召回（直接命中排在其前是正确语义）
    results = await store.search_associative(expanded, limit=12)
    contents = [e.content for e, _ in results]
    assert "只提聚餐的事" in contents
    assert contents.index("火锅聚餐记录0") < contents.index("只提聚餐的事")
