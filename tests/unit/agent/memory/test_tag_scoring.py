"""标签 IDF 评分 / 标签候选通道 / 写入规范化 单元测试。"""

from __future__ import annotations

import json

import pytest

from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.store._shared import idf_tag_score

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
        await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content=f"公共记忆{i}",
            tags=["user:qq:hot"], importance=0.9,
        ))
    rare = await store.add(MemoryEntry(
        memory_type=MemoryType.SEMANTIC, content="稀有标签记忆",
        tags=["topic:unique_x"], importance=0.9,
    ))
    results = await store.search_associative(["user:qq:hot", "topic:unique_x"], limit=3)
    assert results
    # 命中稀有标签的记忆应排第一（IDF 权重远高于高频标签命中）
    assert results[0][0].id == rare


# ==================================================================
# 标签候选通道（hybrid）
# ==================================================================


@pytest.mark.asyncio
async def test_hybrid_recalls_tag_only_match(store) -> None:
    target_id = await store.add(MemoryEntry(
        memory_type=MemoryType.SEMANTIC, content="一段与查询词完全无关的内容 zzz",
        tags=["goal:abc123"], importance=0.8,
    ))
    # 无 query_tags：文本/向量都不命中 → 召不回
    plain = await store.search_hybrid("火锅 烧烤", limit=5, min_score=0.0)
    assert all(e.id != target_id for e, _ in plain)
    # 有 query_tags：标签交集候选通道召回
    tagged = await store.search_hybrid("火锅 烧烤", query_tags=["goal:abc123"], limit=5, min_score=0.0)
    assert any(e.id == target_id for e, _ in tagged)


# ==================================================================
# 写入规范化与近重复提示
# ==================================================================


def test_normalize_tags() -> None:
    from agent.memory.tools import _normalize_tags
    assert _normalize_tags(" topic:LOL ，type:fact,  topic:LOL ,") == ["topic:LOL", "type:fact"]
    assert _normalize_tags("topic:内部  空格") == ["topic:内部 空格"]
    assert _normalize_tags("") == []


@pytest.mark.asyncio
async def test_memorize_tag_hints(store) -> None:
    from agent.memory import tools as mem_tools
    mem_tools.register_memory_tools(store, None)  # type: ignore[arg-type]
    try:
        # 既有高频标签
        for _ in range(2):
            await store.add(MemoryEntry(
                memory_type=MemoryType.SEMANTIC, content="电竞相关",
                tags=["topic:LOL"], importance=0.6,
            ))
        raw = await mem_tools.memorize("打野出新装备了", tags="topic:LOL梗, type:fact")
        data = json.loads(raw)
        assert data["ok"] is True
        assert any("topic:LOL" in h for h in data.get("tag_hints", []))

        # 复用既有标签（非新标签）不给提示
        raw = await mem_tools.memorize("中单又秀了", tags="topic:LOL, type:fact")
        data = json.loads(raw)
        assert "tag_hints" not in data
    finally:
        mem_tools._store = None
