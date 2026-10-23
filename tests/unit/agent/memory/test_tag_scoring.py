"""标签 IDF 评分 / 标签候选通道 / 写入规范化 单元测试。"""

from __future__ import annotations

import pytest

from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.store._shared import idf_tag_score
from agent.memory.tag_query import TagQuery


@pytest.mark.parametrize(
    "filtering,expected",
    [
        (TagQuery(all_of=("future:a/*",), any_of=("role:owner", "role:reader"), none_of=("state:old",)), {"a", "b"}),
        (TagQuery(all_of=("future:a",)), {"a"}),
        (TagQuery(any_of=("future:ab", "future:a%")), {"d", "e"}),
        (TagQuery(none_of=("future:a/*",)), {"d", "e"}),
    ],
)
async def test_sql_and_backend_tag_filters_share_exact_semantics(store, filtering, expected):
    entries = {
        "a": ["future:a", "role:owner"],
        "b": ["future:a/child", "role:reader"],
        "c": ["future:a/child", "role:owner", "state:old"],
        "d": ["future:ab"],
        "e": ["future:a%"],
    }
    for label, tags in entries.items():
        await store.add(MemoryEntry(content=label, tags=tags, memory_type=MemoryType.SEMANTIC))
    results = await store.search_unified("", limit=10, tag_query=filtering)
    assert {result.snippet for result in results} == expected
    assert {label for label, tags in entries.items() if filtering.matches(tags)} == expected


async def test_tag_filter_runs_before_text_and_vector_candidate_limits(store):
    target = await store.add(
        MemoryEntry(content="needle", tags=["future:rare"], embedding=[0.8, 0.6], memory_type=MemoryType.SEMANTIC)
    )
    for i in range(20):
        await store.add(
            MemoryEntry(
                content=f"needle {i}", tags=["future:other"], embedding=[1.0, 0.0], memory_type=MemoryType.SEMANTIC
            )
        )
    results = await store._search.search_hybrid(
        "needle", query_vec=[1.0, 0.0], limit=1, tag_query=TagQuery(all_of=("future:rare",))
    )
    assert [entry.id for entry, score in results] == [target]


# ==================================================================
# IDF 评分
# ==================================================================


def test_idf_score_rare_tag_dominates() -> None:
    df = {"user:qq:1": 300, "topic:罕见": 1}
    total = 400
    # 同样命中 1/2 标签：命中稀有标签的得分应远高于命中高频标签
    rare_hit = idf_tag_score(["user:qq:1", "topic:罕见"], ["topic:罕见"], df, total)
    common_hit = idf_tag_score(["user:qq:1", "topic:罕见"], ["user:qq:1"], df, total)
    assert rare_hit > 0.8
    assert common_hit < 0.15
    assert rare_hit > common_hit * 5


def test_idf_score_empty_and_full_hit() -> None:
    df = {"a": 5}
    assert idf_tag_score([], ["a"], df, 100) == 0.0
    assert idf_tag_score(["a"], [], df, 100) == 0.0
    assert idf_tag_score(["a"], ["a"], df, 100) == 1.0
    # 查询标签不在统计中（全新标签）按最高权重，全命中仍为 1
    assert idf_tag_score(["新标签"], ["新标签"], df, 100) == 1.0


@pytest.mark.asyncio
async def test_associative_prefers_rare_tag_match(store) -> None:
    # 高频公共标签（10 条）
    for i in range(10):
        await store.add(
            MemoryEntry(
                memory_type=MemoryType.SEMANTIC,
                content=f"公共记忆{i}",
                tags=["user:qq:hot"],
                importance=0.9,
            )
        )
    rare = await store.add(
        MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="稀有标签记忆",
            tags=["topic:unique_x"],
            importance=0.9,
        )
    )
    results = await store.search_associative(["user:qq:hot", "topic:unique_x"], limit=3)
    assert results
    # 命中稀有标签的记忆应排第一（IDF 权重远高于高频标签命中）
    assert results[0][0].id == rare


# ==================================================================
# 标签候选通道（hybrid）
# ==================================================================


@pytest.mark.asyncio
async def test_hybrid_recalls_tag_only_match(store) -> None:
    target_id = await store.add(
        MemoryEntry(
            memory_type=MemoryType.SEMANTIC,
            content="一段与查询词完全无关的内容 zzz",
            tags=["goal:abc123"],
            importance=0.8,
        )
    )
    # 无 query_tags：文本/向量都不命中 → 召不回
    plain = await store.search_hybrid("火锅 烧烤", limit=5, min_score=0.0)
    assert all(e.id != target_id for e, _ in plain)
    # 有 query_tags：标签交集候选通道召回
    tagged = await store.search_hybrid("火锅 烧烤", query_tags=["goal:abc123"], limit=5, min_score=0.0)
    assert any(e.id == target_id for e, _ in tagged)


async def test_channel_wins_count_only_returned_results(store, monkeypatch) -> None:
    from agent.memory import metrics

    monkeypatch.setattr(metrics, "_counters", {})
    for i in range(4):
        await store.add(MemoryEntry(
            content=f"needle {i}", tags=["topic:test"], memory_type=MemoryType.SEMANTIC,
        ))
    results = await store.search_hybrid("needle", limit=1, min_score=0)
    assert len(results) == 1
    assert metrics.snapshot().get("recall.win_fts") == 1
    assert sum(value for key, value in metrics.snapshot().items() if key.startswith("recall.win_")) == 1

    monkeypatch.setattr(metrics, "_counters", {})
    results = await store._search.search_hybrid("", limit=1, tag_query=TagQuery(all_of=("topic:test",)))
    assert len(results) == 1
    assert metrics.snapshot().get("recall.win_vec", 0) == 0


# ==================================================================
# 写入规范化（写入闸门行为见 test_tag_gate.py）
# ==================================================================


def test_normalize_tags() -> None:
    from agent.memory.tools import _normalize_tags

    assert _normalize_tags(" topic:LOL ，type:fact,  topic:LOL ,") == ["topic:LOL", "type:fact"]
    assert _normalize_tags("topic:内部  空格") == ["topic:内部 空格"]
    assert _normalize_tags("") == []
