"""sqlite-vec 向量索引测试：检索一致性、双写、回填重建、降级路径。"""

from __future__ import annotations

import math
import time
from unittest.mock import AsyncMock

import numpy as np
import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.memory_utils import cosine_similarity, pack_embedding, rank_embedding_blobs, unpack_embedding
from agent.memory.tag_query import TagQuery

_DIMS = 8


def _unit_vec(i: int) -> list[float]:
    """第 i 维为 1 的单位向量。"""
    v = [0.0] * _DIMS
    v[i % _DIMS] = 1.0
    return v


def _entry(content: str, embedding: list[float] | None = None) -> MemoryEntry:
    return MemoryEntry(
        memory_type=MemoryType.SEMANTIC,
        content=content,
        timestamp=time.time(),
        embedding=embedding,
    )


def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


async def test_vec_available_after_init(store: MemoryStore) -> None:
    await store._get_db()
    assert store._vec_available is True


async def test_search_matches_fallback_order(store: MemoryStore) -> None:
    vecs = [_norm([1.0, 0.1 * i] + [0.0] * (_DIMS - 2)) for i in range(5)]
    for i, v in enumerate(vecs):
        await store.add(_entry(f"记忆{i}", embedding=v))
    query = _norm([1.0, 0.2] + [0.0] * (_DIMS - 2))

    vec_results = await store.search_vector(query, limit=3, min_score=0.0)
    assert [r[0].content for r in vec_results]

    store._vec_available = False  # 强制降级
    try:
        fb_results = await store.search_vector(query, limit=3, min_score=0.0)
    finally:
        store._vec_available = True

    assert [r[0].id for r in vec_results] == [r[0].id for r in fb_results]
    for (_, s1), (_, s2) in zip(vec_results, fb_results, strict=False):
        assert s1 == pytest.approx(s2, abs=1e-5)


async def test_min_score_filter(store: MemoryStore) -> None:
    await store.add(_entry("同向", embedding=_unit_vec(0)))
    await store.add(_entry("正交", embedding=_unit_vec(1)))
    results = await store.search_vector(_unit_vec(0), limit=10, min_score=0.5)
    assert len(results) == 1
    assert results[0][0].content == "同向"
    assert results[0][1] == pytest.approx(1.0, abs=1e-5)


async def test_update_and_delete_synced(store: MemoryStore) -> None:
    mid = await store.add(_entry("原始", embedding=_unit_vec(0)))
    entry = await store.get(mid)
    assert entry is not None
    await store.update(entry, new_embedding=_unit_vec(1))

    results = await store.search_vector(_unit_vec(1), limit=5, min_score=0.5)
    assert any(r[0].id == mid for r in results)
    results = await store.search_vector(_unit_vec(0), limit=5, min_score=0.5)
    assert all(r[0].id != mid for r in results)

    await store.delete(mid)
    results = await store.search_vector(_unit_vec(1), limit=5, min_score=0.0)
    assert all(r[0].id != mid for r in results)


async def test_content_update_invalidates_snapshot_embedding(store: MemoryStore) -> None:
    mid = await store.add(_entry("旧正文", embedding=_unit_vec(0)))
    entry = await store.get(mid)
    assert entry is not None and entry.embedding == _unit_vec(0)
    entry.content = "新正文"
    assert await store.update(entry, clear_embedding=True)
    assert entry.embedding is None

    refreshed = await store.get(mid)
    assert refreshed is not None and refreshed.embedding is None
    assert await store.search_vector(_unit_vec(0), limit=5, min_score=0.5) == []


async def test_snapshot_embedding_does_not_replace_current_vector(store: MemoryStore) -> None:
    mid = await store.add(_entry("正文", embedding=_unit_vec(0)))
    entry = await store.get(mid)
    assert entry is not None
    entry.embedding = _unit_vec(1)
    entry.importance = 0.7
    assert await store.update(entry)

    assert any(
        result.id == mid
        for result, _score in await store.search_vector(_unit_vec(0), limit=5, min_score=0.5)
    )
    assert all(
        result.id != mid
        for result, _score in await store.search_vector(_unit_vec(1), limit=5, min_score=0.5)
    )


async def test_ann_ignores_residual_index_without_authoritative_embedding(store: MemoryStore) -> None:
    mid = await store.add(_entry("旧正文", embedding=_unit_vec(0)))
    db = await store._get_db()
    await db.execute("UPDATE memories SET embedding_blob=NULL WHERE id=?", (mid,))
    await db.commit()

    assert await store.search_vector(_unit_vec(0), limit=5, min_score=0.0) == []


async def test_expected_version_conflict_does_not_write(store: MemoryStore) -> None:
    mid = await store.add(_entry("初版", embedding=_unit_vec(0)))
    entry = await store.get(mid)
    assert entry is not None
    entry.content = "冲突写入"
    assert not await store.update(entry, expected_version=entry.version + 1)
    refreshed = await store.get(mid)
    assert refreshed is not None and refreshed.content == "初版"
    assert refreshed.embedding == _unit_vec(0)


async def test_stale_index_rebuilt_on_open(tmp_path) -> None:
    db_path = str(tmp_path / "memory.sqlite3")
    s1 = MemoryStore(db_path)
    for i in range(3):
        await s1.add(_entry(f"记忆{i}", embedding=_unit_vec(i)))
    db = await s1._get_db()
    await db.execute("DELETE FROM memories_vec")
    await db.commit()
    await s1.close()

    s2 = MemoryStore(db_path)
    try:
        results = await s2.search_vector(_unit_vec(0), limit=10, min_score=0.0)
        assert len(results) == 3  # 计数失配触发全量回填
    finally:
        await s2.close()


async def test_lazy_table_creation_on_first_embedding(store: MemoryStore) -> None:
    # 全新库无 embedding：init 阶段不建表，首次写入时惰性创建
    await store._get_db()
    assert store._vec_dims is None
    await store.add(_entry("首条", embedding=_unit_vec(0)))
    assert store._vec_dims == _DIMS
    results = await store.search_vector(_unit_vec(0), limit=5, min_score=0.0)
    assert len(results) == 1


async def test_chunks_vec_search(store: MemoryStore) -> None:
    now_ns = time.time_ns()
    chunks = [
        {
            "id": f"memory/a.md:{i}-{i + 1}", "path": "memory/a.md",
            "start_line": i, "end_line": i + 1, "hash": f"h{i}",
            "text": f"段落{i}", "embedding": pack_embedding(_unit_vec(i)),
            "updated_ns": now_ns,
        }
        for i in range(3)
    ]
    await store.upsert_chunks(chunks)

    results = await store.search_chunks_vector(_unit_vec(0), limit=5, min_score=0.5)
    assert len(results) == 1
    assert results[0]["id"] == "memory/a.md:0-1"
    store._vec_available = False
    fallback = await store.search_chunks_vector(_unit_vec(0), limit=5, min_score=0.5)
    assert fallback == results
    store._vec_available = True

    await store.delete_chunks_by_path("memory/a.md")
    results = await store.search_chunks_vector(_unit_vec(0), limit=5, min_score=0.0)
    assert results == []


async def test_fallback_path_intact_when_vec_disabled(store: MemoryStore) -> None:
    store._vec_available = False
    await store.add(_entry("降级", embedding=_unit_vec(0)))
    results = await store.search_vector(_unit_vec(0), limit=5, min_score=0.5)
    assert len(results) == 1
    assert results[0][0].content == "降级"


def test_vectorized_scores_match_double_precision_reference() -> None:
    rng = np.random.default_rng(7)
    query = rng.normal(size=1024).tolist()
    blobs = [pack_embedding(row.tolist()) for row in rng.normal(size=(120, 1024))]
    expected = []
    for i, blob in enumerate(blobs):
        row = unpack_embedding(blob)
        score = math.fsum(a * b for a, b in zip(query, row, strict=True)) / (
            math.sqrt(math.fsum(v * v for v in query)) * math.sqrt(math.fsum(v * v for v in row))
        )
        expected.append((i, score))
        assert cosine_similarity(query, row) == pytest.approx(score, abs=1e-14)
    expected.sort(key=lambda item: item[1], reverse=True)
    actual = rank_embedding_blobs(query, blobs, limit=17, min_score=-1)
    assert [i for i, _ in actual] == [i for i, _ in expected[:17]]
    assert [score for _, score in actual] == pytest.approx([score for _, score in expected[:17]], abs=1e-14)


def test_batch_scoring_rejects_invalid_vectors_and_keeps_stable_ties() -> None:
    blobs = [pack_embedding(v) for v in ([1, 0], [1, 0], [-1, 0], [0, 0], [float("nan"), 1],
                                       [float("inf"), 0], [1])]
    blobs.extend([None, b"bad"])
    assert rank_embedding_blobs([1, 0], blobs, limit=10, min_score=-1) == [(0, 1), (1, 1), (3, 0), (2, -1)]
    assert rank_embedding_blobs([1, 0], blobs, limit=1, min_score=1) == [(0, 1)]
    assert rank_embedding_blobs([float("nan"), 0], blobs, limit=2, min_score=0) == []
    assert rank_embedding_blobs([], blobs, limit=2, min_score=0) == []
    for left, right in (([], []), ([1], [1, 2]), ([0, 0], [1, 0]), ([float("inf")], [1])):
        assert cosine_similarity(left, right) == 0


@pytest.mark.parametrize("tag_query", [
    TagQuery(all_of=("topic:rare",)),
    TagQuery(any_of=("topic:rare", "topic:missing"), none_of=("state:exclude",)),
    TagQuery(all_of=("topic:tree/*",), none_of=("state:exclude",)),
    TagQuery(all_of=("topic:missing",)),
    TagQuery(),
])
async def test_filtered_knn_matches_exact_scan_without_overfetch(
    store: MemoryStore, monkeypatch: pytest.MonkeyPatch, tag_query: TagQuery,
) -> None:
    # 高分干扰项多于 limit×20；稀有标签命中仍必须在限量前入选。
    for i in range(48):
        entry = _entry(f"文档{i}", [1, i / 50] + [0] * 6)
        entry.tags = (["topic:rare", "topic:tree/leaf"] if i >= 44 else ["topic:rare-extra", "topic:treeish"])
        if i == 44:
            entry.tags.append("state:exclude")
        if i < 3:
            entry.importance = 0
        await store.add(entry)
    query = _unit_vec(0)
    scan = store._search._fetch_vector_batch
    monkeypatch.setattr(store._search, "_fetch_vector_batch", AsyncMock(side_effect=AssertionError("unexpected scan")))
    actual = await store._search.search_vector(query, limit=2, min_score=0, tag_query=tag_query)
    store._search._fetch_vector_batch.assert_not_awaited()
    monkeypatch.setattr(store._search, "_fetch_vector_batch", scan)
    store._vec_available = False
    expected = await store._search.search_vector(query, limit=2, min_score=0, tag_query=tag_query)
    assert [entry.id for entry, _ in actual] == [entry.id for entry, _ in expected]
    assert [score for _, score in actual] == pytest.approx([score for _, score in expected], abs=1e-6)
    assert all(tag_query.matches(entry.tags) for entry, _ in actual)


async def test_knn_failure_uses_same_filtered_batch_search(store: MemoryStore, monkeypatch) -> None:
    target = _entry("保留的证据", _unit_vec(0))
    target.tags = ["topic:keep"]
    mid = await store.add(target)
    await store.add(_entry("范围外", _unit_vec(0)))
    monkeypatch.setattr(store._search, "_search_vector_vec", AsyncMock(side_effect=RuntimeError("vec unavailable")))
    hits = await store._search.search_vector(_unit_vec(0), tag_query=TagQuery(all_of=("topic:keep",)))
    assert [entry.id for entry, _ in hits] == [mid]
