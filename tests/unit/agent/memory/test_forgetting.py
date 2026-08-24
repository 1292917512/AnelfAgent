"""遗忘与强化机制（memory_store）单元测试。"""

from __future__ import annotations

import time

import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType


def _entry(
    content: str = "测试记忆",
    memory_type: MemoryType = MemoryType.SEMANTIC,
    importance: float = 0.5,
    age_hours: float = 0.0,
    access_count: int = 0,
) -> MemoryEntry:
    return MemoryEntry(
        memory_type=memory_type,
        content=content,
        importance=importance,
        timestamp=time.time() - age_hours * 3600,
        access_count=access_count,
    )


class TestEffectiveScore:
    def test_permanent_always_max(self) -> None:
        e = _entry(memory_type=MemoryType.PERMANENT, importance=0.1, age_hours=9999)
        assert MemoryStore.compute_effective_score(e) == 1.0

    def test_fresh_high_importance(self) -> None:
        e = _entry(importance=0.9, age_hours=0)
        score = MemoryStore.compute_effective_score(e)
        assert score == pytest.approx(0.9, abs=0.01)

    def test_decay_reduces_score(self) -> None:
        fresh = _entry(importance=0.8, age_hours=0)
        old = _entry(importance=0.8, age_hours=30 * 24)  # 一个半衰期
        assert MemoryStore.compute_effective_score(old) < MemoryStore.compute_effective_score(fresh)

    def test_access_reinforcement(self) -> None:
        plain = _entry(importance=0.5, access_count=0)
        reinforced = _entry(importance=0.5, access_count=20)
        assert (
            MemoryStore.compute_effective_score(reinforced)
            > MemoryStore.compute_effective_score(plain)
        )


class TestForgetWeakMemories:
    async def test_forgets_old_weak(self, store: MemoryStore) -> None:
        # 60 天前的低重要性、从未访问的记忆 → 应被遗忘了
        weak = _entry("陈年旧事", importance=0.1, age_hours=60 * 24, access_count=0)
        weak_id = await store.add(weak)
        # 新记忆不应被遗忘（最小年龄保护）
        fresh = _entry("新记忆", importance=0.1, age_hours=1, access_count=0)
        fresh_id = await store.add(fresh)

        report = await store.forget_weak_memories(min_age_days=30, score_threshold=0.08, min_keep_per_type=0)
        assert report["count"] == 1
        assert report["forgotten"][0]["id"] == weak_id
        assert await store.get(fresh_id) is not None

    async def test_forgetting_is_archived_and_restorable(self, store: MemoryStore) -> None:
        weak_id = await store.add(_entry(
            "可恢复的记忆", importance=0.1, age_hours=90 * 24, access_count=0,
        ))
        report = await store.forget_weak_memories(min_age_days=30, score_threshold=0.08, min_keep_per_type=0)
        assert report["count"] == 1
        # 已归档：不在活跃库，但在归档表
        assert await store.get(weak_id) is None
        archived = await store.list_archived()
        assert any(a["id"] == weak_id for a in archived)
        # 可恢复
        assert await store.restore_memory(weak_id)
        restored = await store.get(weak_id)
        assert restored is not None
        assert restored.content == "可恢复的记忆"

    async def test_permanent_exempt(self, store: MemoryStore) -> None:
        pid = await store.add(_entry(
            "永久规则", memory_type=MemoryType.PERMANENT,
            importance=0.1, age_hours=365 * 24, access_count=0,
        ))
        report = await store.forget_weak_memories(min_age_days=1, score_threshold=0.99, min_keep_per_type=0)
        assert report["count"] == 0
        assert await store.get(pid) is not None

    async def test_reinforced_memory_survives(self, store: MemoryStore) -> None:
        # 老记忆但频繁访问 → 有效分高 → 不遗忘
        strong = _entry("常用知识", importance=0.5, age_hours=60 * 24, access_count=100)
        strong_id = await store.add(strong)
        report = await store.forget_weak_memories(min_age_days=30, score_threshold=0.08, min_keep_per_type=0)
        assert report["count"] == 0
        assert await store.get(strong_id) is not None


class TestEnforceTypeLimits:
    async def test_excess_removed(self, store: MemoryStore) -> None:
        for i in range(5):
            await store.add(_entry(f"记忆{i}", importance=0.1 * (i + 1)))
        removed = await store.enforce_type_limits(max_per_type=3)
        assert removed.get("semantic") == 2
        assert await store.count(MemoryType.SEMANTIC) == 3
        # 保留的应是重要性最高的
        entries = await store.list_recent(limit=10, memory_type=MemoryType.SEMANTIC)
        importances = [e.importance for e in entries]
        assert 0.5 in importances and 0.1 not in importances

    async def test_permanent_unlimited(self, store: MemoryStore) -> None:
        for i in range(5):
            await store.add(_entry(f"永久{i}", memory_type=MemoryType.PERMANENT))
        removed = await store.enforce_type_limits(max_per_type=2)
        assert "permanent" not in removed
        assert await store.count(MemoryType.PERMANENT) == 5


class TestReinforcement:
    async def test_record_access_boosts_importance(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("知识", importance=0.5))
        await store.record_access([mid])
        entry = await store.get(mid)
        assert entry.access_count == 1
        assert entry.importance == pytest.approx(0.52, abs=0.001)

    async def test_importance_capped(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("知识", importance=0.99))
        await store.record_access([mid, mid])
        entry = await store.get(mid)
        assert entry.importance <= 1.0

    async def test_permanent_not_boosted(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("永久", memory_type=MemoryType.PERMANENT, importance=0.5))
        await store.record_access([mid])
        entry = await store.get(mid)
        assert entry.access_count == 1
        assert entry.importance == 0.5


class TestSimilarMerge:
    async def test_find_and_merge_similar(self, store: MemoryStore) -> None:
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [0.99, 0.01, 0.0]
        a_id = await store.add(_entry("相似记忆A", importance=0.6))
        b_id = await store.add(_entry("相似记忆B", importance=0.4))
        await store.update(MemoryEntry(
            id=a_id, memory_type=MemoryType.SEMANTIC, content="相似记忆A",
            importance=0.6, embedding=vec_a, tags=["topic:x"],
        ))
        await store.update(MemoryEntry(
            id=b_id, memory_type=MemoryType.SEMANTIC, content="相似记忆B",
            importance=0.4, embedding=vec_b, tags=["topic:y"],
        ))

        pairs = await store.find_similar_memories(0.92)
        assert len(pairs) == 1
        assert pairs[0][2] > 0.92

        keep, drop = (a_id, b_id) if pairs[0][0].id == a_id else (b_id, a_id)
        assert await store.merge_pair(keep, drop)
        assert await store.get(drop) is None
        merged = await store.get(keep)
        assert set(merged.tags) == {"topic:x", "topic:y"}


class TestRelaxImportance:
    async def test_stale_high_importance_relaxed(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("强化过的记忆", importance=0.9))
        adjusted = await store.relax_importance(stale_days=14, rate=0.1)
        assert adjusted == 1
        entry = await store.get(mid)
        # 0.5 + (0.9 - 0.5) * 0.9 = 0.86
        assert entry.importance == pytest.approx(0.86, abs=0.001)

    async def test_recently_accessed_exempt(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("刚被想起的记忆", importance=0.9))
        await store.record_access([mid])
        adjusted = await store.relax_importance(stale_days=14, rate=0.1)
        assert adjusted == 0
        entry = await store.get(mid)
        assert entry.importance == pytest.approx(0.92, abs=0.001)

    async def test_permanent_exempt(self, store: MemoryStore) -> None:
        await store.add(_entry("永久记忆", memory_type=MemoryType.PERMANENT, importance=1.0))
        assert await store.relax_importance(stale_days=14, rate=0.1) == 0

    async def test_below_baseline_untouched(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("低重要性记忆", importance=0.3))
        assert await store.relax_importance(stale_days=14, rate=0.1) == 0
        entry = await store.get(mid)
        assert entry.importance == pytest.approx(0.3, abs=0.001)

    async def test_zero_rate_disabled(self, store: MemoryStore) -> None:
        await store.add(_entry("记忆", importance=0.9))
        assert await store.relax_importance(stale_days=14, rate=0.0) == 0


class TestManualForgetArchived:
    async def test_forget_archives_and_restorable(self, store: MemoryStore) -> None:
        import json

        from agent.memory import tools as mem_tools

        mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
        try:
            mid = await store.add(_entry("待遗忘的记忆", importance=0.7))

            result = json.loads(await mem_tools.forget(mid))
            assert result["ok"] is True
            assert await store.get(mid) is None
            archived = await store.list_archived()
            assert any(a["id"] == mid and a["reason"] == "manual_forget" for a in archived)

            assert await store.restore_memory(mid) is True
        finally:
            mem_tools.memory_tools_port.unbind()
        assert (await store.get(mid)).content == "待遗忘的记忆"


class TestCleanupLowImportanceArchived:
    async def test_cleanup_archives_instead_of_delete(self, store: MemoryStore) -> None:
        mid = await store.add(_entry(
            "极低重要性老记忆", importance=0.01, age_hours=24 * 100,
        ))
        removed = await store.cleanup_low_importance(threshold=0.05, max_age_hours=24 * 90)
        assert removed == 1
        assert await store.get(mid) is None
        archived = await store.list_archived()
        assert any(a["id"] == mid for a in archived)
        assert await store.restore_memory(mid) is True
