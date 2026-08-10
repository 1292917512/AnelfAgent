"""标签智能（TagIntelligence）单元测试：共现联想 / 查询提及识别 / 种子三层扩展。"""

from __future__ import annotations

import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    yield s
    await s.close()


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
