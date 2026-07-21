"""sqlite-vec 向量索引测试：检索一致性、双写、回填重建、降级路径。"""

from __future__ import annotations

import math
import time

import pytest

pytest.importorskip("sqlite_vec")

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.memory_utils import pack_embedding

_DIMS = 8


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    yield s
    await s.close()


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
    for (_, s1), (_, s2) in zip(vec_results, fb_results):
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
    entry.embedding = _unit_vec(1)
    await store.update(entry)

    results = await store.search_vector(_unit_vec(1), limit=5, min_score=0.5)
    assert any(r[0].id == mid for r in results)
    results = await store.search_vector(_unit_vec(0), limit=5, min_score=0.5)
    assert all(r[0].id != mid for r in results)

    await store.delete(mid)
    results = await store.search_vector(_unit_vec(1), limit=5, min_score=0.0)
    assert all(r[0].id != mid for r in results)


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

    await store.delete_chunks_by_path("memory/a.md")
    results = await store.search_chunks_vector(_unit_vec(0), limit=5, min_score=0.0)
    assert results == []


async def test_fallback_path_intact_when_vec_disabled(store: MemoryStore) -> None:
    store._vec_available = False
    await store.add(_entry("降级", embedding=_unit_vec(0)))
    results = await store.search_vector(_unit_vec(0), limit=5, min_score=0.5)
    assert len(results) == 1
    assert results[0][0].content == "降级"
