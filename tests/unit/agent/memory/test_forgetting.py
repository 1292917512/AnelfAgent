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

    async def test_access_shield_slows_relax(self, store: MemoryStore) -> None:
        # 检索练习效应：历史访问越多的记忆回归越慢
        plain_id = await store.add(_entry("很少想起", importance=0.9))
        shielded_id = await store.add(_entry("常被想起", importance=0.9))
        db = await store._get_db()
        await db.execute(
            "UPDATE memories SET access_count=20 WHERE id=?", (shielded_id,),
        )
        await db.commit()

        adjusted = await store.relax_importance(stale_days=14, rate=0.1)
        assert adjusted == 2
        plain = await store.get(plain_id)
        shielded = await store.get(shielded_id)
        # 无护盾：0.5 + 0.4 × 0.9 = 0.86；20 次访问护盾 ÷(1+ln20) ≈ ÷4.0 → 0.89
        assert plain.importance == pytest.approx(0.86, abs=0.001)
        assert shielded.importance == pytest.approx(0.89, abs=0.001)
        assert shielded.importance > plain.importance


class TestPurgeTombstone:
    async def _archive_with_backdate(
        self, store: MemoryStore, content: str, days: int = 100,
    ) -> int:
        """新增并归档一条记忆，归档时间回拨指定天数（满足 purge 保留期）。"""
        mid = await store.add(_entry(content, importance=0.6))
        assert await store.archive_memory(mid)
        db = await store._get_db()
        backdated_ns = int((time.time() - days * 86400) * 1e9)
        await db.execute(
            "UPDATE memories_archive SET archived_at_ns=? WHERE id=?",
            (backdated_ns, mid),
        )
        await db.commit()
        return mid

    async def test_purge_leaves_tombstone(self, store: MemoryStore) -> None:
        mid = await self._archive_with_backdate(store, "tombanchor 会被物理删除的记忆")

        deleted = await store.purge_archived_memories(90)
        assert deleted == 1
        assert await store.count_archived() == 0
        assert await store.count_tombstones() == 1
        # 归档原文已删除，墓碑仍可经 gist 检索到
        hits = await store.search_forgotten("tombanchor")
        assert len(hits) == 1
        assert hits[0]["kind"] == "tombstone"
        assert hits[0]["memory_id"] == mid
        assert "会被物理删除" in hits[0]["gist"]
        assert hits[0]["score"] <= 0.3

    async def test_tombstone_gist_truncated(self, store: MemoryStore) -> None:
        long_content = "长内容 " + "字" * 500
        await self._archive_with_backdate(store, long_content)
        await store.purge_archived_memories(90)
        db = await store._get_db()
        cursor = await db.execute("SELECT gist FROM memories_tombstone")
        row = await cursor.fetchone()
        assert row is not None
        assert row["gist"] == long_content[:200]

    async def test_tombstone_cap_fifo(self, store: MemoryStore) -> None:
        oldest = await self._archive_with_backdate(store, "capanchor 最老的记忆", days=120)
        await self._archive_with_backdate(store, "capanchor 中间的记忆", days=110)
        newest = await self._archive_with_backdate(store, "capanchor 最新的记忆", days=100)

        deleted = await store.purge_archived_memories(90, tombstone_max_rows=2)
        assert deleted == 3
        assert await store.count_tombstones() == 2
        # FIFO：最老归档的墓碑被淘汰，较新的两条保留
        hits = await store.search_forgotten("capanchor")
        kept_ids = {h["memory_id"] for h in hits}
        assert kept_ids != set()
        assert oldest not in kept_ids
        assert newest in kept_ids

    async def test_tombstone_cap_zero_unlimited(self, store: MemoryStore) -> None:
        for i in range(3):
            await self._archive_with_backdate(store, f"zeroanchor 记忆{i}", days=100 + i)
        await store.purge_archived_memories(90, tombstone_max_rows=0)
        assert await store.count_tombstones() == 3


class TestSearchForgotten:
    async def test_archived_keyword_hit(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("forgotanchor 被遗忘的知识", importance=0.6))
        assert await store.archive_memory(mid)

        hits = await store.search_forgotten("forgotanchor")
        assert len(hits) == 1
        assert hits[0]["kind"] == "archived"
        assert hits[0]["id"] == mid
        assert hits[0]["score"] == pytest.approx(0.35)
        assert "被遗忘的知识" in hits[0]["content"]

    async def test_archived_vector_hit_outranks_keyword(self, store: MemoryStore) -> None:
        entry = _entry("vecanchor 向量记忆", importance=0.6)
        entry.embedding = [1.0, 0.0, 0.0]
        mid = await store.add(entry)
        assert await store.archive_memory(mid)

        hits = await store.search_forgotten("无关键词命中", [1.0, 0.0, 0.0])
        assert len(hits) == 1
        assert hits[0]["kind"] == "archived"
        assert hits[0]["score"] == pytest.approx(1.0, abs=0.01)

    async def test_active_memory_not_in_forgotten(self, store: MemoryStore) -> None:
        await store.add(_entry("liveanchor 活跃记忆", importance=0.6))
        hits = await store.search_forgotten("liveanchor")
        assert hits == []


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
