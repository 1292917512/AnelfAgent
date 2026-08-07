"""关系快照注入单元测试：内容字节稳定性（前缀缓存友好）与变更即时可见。"""

from __future__ import annotations

import pytest

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_store import MemoryStore


class _NullEmbedder:
    available = False

    async def embed_query(self, _query: str):
        return None


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_relation_snippets_byte_stable_across_rounds(store) -> None:
    """图谱与参与人不变时，多轮注入内容字节一致（不伤前缀缓存命中）。"""
    retriever = MemoryRetriever(store, _NullEmbedder())
    await store.graph.add_relation("user:qq:1", "朋友", "user:qq:2")
    await store.graph.add_relation("user:qq:1", "喜欢", "topic:火锅", strength=0.9)

    scopes = ["user_qq:1"]
    first = await retriever.load_relation_snippets(scopes)
    second = await retriever.load_relation_snippets(scopes)
    assert len(first) == 1
    assert first[0]["content"] == second[0]["content"]
    # 高强度边排前（确定性排序）
    assert first[0]["content"].index("喜欢") < first[0]["content"].index("朋友")


@pytest.mark.asyncio
async def test_relation_snippets_reflect_changes_immediately(store) -> None:
    """无缓存机制：图谱变更下一轮即反映。"""
    retriever = MemoryRetriever(store, _NullEmbedder())
    await store.graph.add_relation("user:qq:1", "朋友", "user:qq:2")
    scopes = ["user_qq:1"]
    assert "同事" not in (await retriever.load_relation_snippets(scopes))[0]["content"]
    await store.graph.add_relation("user:qq:1", "同事", "user:qq:3")
    assert "同事" in (await retriever.load_relation_snippets(scopes))[0]["content"]


@pytest.mark.asyncio
async def test_relation_snippets_empty_when_no_graph(store) -> None:
    retriever = MemoryRetriever(store, _NullEmbedder())
    assert await retriever.load_relation_snippets(["user_qq:9"]) == []
    assert await retriever.load_relation_snippets([]) == []


@pytest.mark.asyncio
async def test_relation_snippets_order_immune_to_edge_updates(store) -> None:
    """同强度边被更新（updated_ns 变化）不影响注入字节序——多实体群聊常态下的缓存稳定性。"""
    retriever = MemoryRetriever(store, _NullEmbedder())
    e1 = await store.graph.add_relation("user:qq:1", "同事", "user:qq:2", strength=0.7)
    await store.graph.add_relation("user:qq:1", "朋友", "user:qq:3", strength=0.7)
    scopes = ["user_qq:1"]
    before = (await retriever.load_relation_snippets(scopes))[0]["content"]
    # 更新第一条边的证据（updated_ns 改变，内容集未变）
    await store.graph.update_relation(e1["id"], evidence="补充证据")
    after = (await retriever.load_relation_snippets(scopes))[0]["content"]
    # 字节仅差异在证据文本，顺序不变
    assert before.index("同事") < after.index("朋友")
    assert "补充证据" in after
