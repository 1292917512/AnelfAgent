"""合并统一语义（merge_into_keep / merge_memories）与 mem:ID 分层解析（resolve）测试。

覆盖：keep 原地演进、drop 软标记重定向、系统独占条目准入、链式解析、
归档/墓碑/缺失分层、purge 墓碑 redirect_to、审计 actor 与 update 旧值。
"""

from __future__ import annotations

import time

import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import GOAL_SOURCE, MemoryEntry, MemoryType


def _entry(
    content: str = "测试记忆",
    memory_type: MemoryType = MemoryType.SEMANTIC,
    importance: float = 0.5,
    age_hours: float = 0.0,
    access_count: int = 0,
    source: str = "",
) -> MemoryEntry:
    return MemoryEntry(
        memory_type=memory_type,
        content=content,
        source=source,
        importance=importance,
        timestamp=time.time() - age_hours * 3600,
        access_count=access_count,
    )


class TestMergeIntoKeep:
    async def test_keep_evolves_in_place(self, store: MemoryStore) -> None:
        keep_id = await store.add(_entry("存活条目", importance=0.6, access_count=3))
        drop_id = await store.add(_entry("被并入条目", importance=0.4, access_count=2))

        result = await store.merge_into_keep(
            keep_id, [drop_id], new_content="合成后的完整表述", actor="test",
        )
        assert result == keep_id

        keep = await store.get(keep_id)
        assert keep is not None
        assert keep.content == "合成后的完整表述"
        assert keep.importance == pytest.approx(0.6)
        assert keep.access_count == 5
        assert keep.metadata["merged_from"] == [drop_id]
        assert keep.version == 2

        drop = await store.get(drop_id)
        assert drop is not None
        assert drop.importance == 0
        assert drop.metadata["merged_into"] == keep_id

    async def test_keep_content_preserved_without_new_content(self, store: MemoryStore) -> None:
        keep_id = await store.add(_entry("高相似条目A", importance=0.6))
        drop_id = await store.add(_entry("高相似条目B", importance=0.4))

        assert await store.merge_into_keep(keep_id, [drop_id]) == keep_id
        keep = await store.get(keep_id)
        assert keep is not None
        assert keep.content == "高相似条目A"
        assert keep.version == 2

    async def test_tags_merged_as_union(self, store: MemoryStore) -> None:
        keep_id = await store.add(_entry("A", importance=0.6))
        drop_id = await store.add(_entry("B", importance=0.4))
        await store.update(MemoryEntry(
            id=keep_id, memory_type=MemoryType.SEMANTIC, content="A",
            importance=0.6, tags=["topic:x"],
        ))
        await store.update(MemoryEntry(
            id=drop_id, memory_type=MemoryType.SEMANTIC, content="B",
            importance=0.4, tags=["topic:y", "topic:x"],
        ))
        await store.merge_into_keep(keep_id, [drop_id])
        keep = await store.get(keep_id)
        assert keep is not None
        assert set(keep.tags) == {"topic:x", "topic:y"}

    async def test_protected_drop_rejected(self, store: MemoryStore) -> None:
        keep_id = await store.add(_entry("普通条目", importance=0.5))
        perm_id = await store.add(_entry(
            "永久规则", memory_type=MemoryType.PERMANENT, importance=1.0,
        ))
        # 唯一 drop 非法 → 整体 None，双方均不受影响
        assert await store.merge_into_keep(keep_id, [perm_id]) is None
        perm = await store.get(perm_id)
        assert perm is not None and perm.importance == 1.0

    async def test_exclusive_entries_skipped_but_rest_merged(self, store: MemoryStore) -> None:
        keep_id = await store.add(_entry("存活", importance=0.6))
        goal_id = await store.add(_entry("规划条目", source=GOAL_SOURCE, importance=0.5))
        entity_id = await store.add(_entry(
            "画像条目", memory_type=MemoryType.ENTITY, importance=0.5,
        ))
        normal_id = await store.add(_entry("普通被并入", importance=0.4))

        result = await store.merge_into_keep(keep_id, [goal_id, entity_id, normal_id])
        assert result == keep_id
        # 系统独占条目原样保留；普通条目被并入
        assert (await store.get(goal_id)).importance != 0
        assert (await store.get(entity_id)).importance != 0
        assert (await store.get(normal_id)).metadata["merged_into"] == keep_id

    async def test_protected_keep_rejected(self, store: MemoryStore) -> None:
        perm_id = await store.add(_entry(
            "永久规则", memory_type=MemoryType.PERMANENT, importance=1.0,
        ))
        normal_id = await store.add(_entry("普通条目", importance=0.5))
        assert await store.merge_into_keep(perm_id, [normal_id]) is None
        assert (await store.get(normal_id)).importance != 0

    async def test_self_merge_and_missing_ignored(self, store: MemoryStore) -> None:
        keep_id = await store.add(_entry("存活", importance=0.6))
        other_id = await store.add(_entry("其他", importance=0.4))
        result = await store.merge_into_keep(keep_id, [keep_id, 99999, other_id])
        assert result == keep_id
        assert (await store.get(other_id)).metadata["merged_into"] == keep_id

    async def test_keep_missing_returns_none(self, store: MemoryStore) -> None:
        other_id = await store.add(_entry("其他"))
        assert await store.merge_into_keep(99999, [other_id]) is None

    async def test_drop_removed_from_cognee_queue_as_delete(self, store: MemoryStore) -> None:
        store.set_cognee_projection_enabled(True)
        keep_id = await store.add(_entry("存活", importance=0.6))
        drop_id = await store.add(_entry("被并入", importance=0.4))
        await store.merge_into_keep(keep_id, [drop_id])
        db = await store._get_db()
        cursor = await db.execute(
            "SELECT operation FROM cognee_sync_queue WHERE entry_id=? "
            "ORDER BY id DESC LIMIT 1",
            (drop_id,),
        )
        row = await cursor.fetchone()
        assert row is not None and row["operation"] == "delete"


class TestMergeMemories:
    async def test_highest_effective_score_survives(self, store: MemoryStore) -> None:
        weak_id = await store.add(_entry("弱条目", importance=0.3, age_hours=90 * 24))
        strong_id = await store.add(_entry("强条目", importance=0.9, access_count=10))

        keep_id = await store.merge_memories(
            [weak_id, strong_id], "合并后的表述", actor="test",
        )
        assert keep_id == strong_id
        keep = await store.get(strong_id)
        assert keep is not None and keep.content == "合并后的表述"
        assert (await store.get(weak_id)).metadata["merged_into"] == strong_id

    async def test_all_exclusive_returns_zero(self, store: MemoryStore) -> None:
        p1 = await store.add(_entry("永久1", memory_type=MemoryType.PERMANENT))
        p2 = await store.add(_entry("永久2", memory_type=MemoryType.PERMANENT))
        assert await store.merge_memories([p1, p2], "合并") == 0

    async def test_too_few_returns_zero(self, store: MemoryStore) -> None:
        only = await store.add(_entry("仅一条"))
        assert await store.merge_memories([only], "合并") == 0
        assert await store.merge_memories([only, 99999], "合并") == 0
        assert await store.merge_memories([], "合并") == 0


class TestResolve:
    async def test_active_direct(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("活跃条目"))
        resolution = await store.resolve(mid)
        assert resolution["status"] == "active"
        assert resolution["entry"].id == mid
        assert resolution["chain"] == []

    async def test_merged_chain_two_hops(self, store: MemoryStore) -> None:
        a_id = await store.add(_entry("A", importance=0.4))
        b_id = await store.add(_entry("B", importance=0.5))
        c_id = await store.add(_entry("C", importance=0.6))
        await store.merge_into_keep(b_id, [a_id])
        await store.merge_into_keep(c_id, [b_id])

        resolution = await store.resolve(a_id)
        assert resolution["status"] == "active"
        assert resolution["entry"].id == c_id
        assert resolution["chain"] == [a_id, b_id]

    async def test_archived_direct(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("将归档"))
        await store.archive_memory(mid, "test_archive")
        resolution = await store.resolve(mid)
        assert resolution["status"] == "archived"
        assert resolution["row"]["id"] == mid
        assert resolution["row"]["reason"] == "test_archive"

    async def test_merged_then_keep_archived(self, store: MemoryStore) -> None:
        a_id = await store.add(_entry("A", importance=0.4))
        b_id = await store.add(_entry("B", importance=0.6))
        await store.merge_into_keep(b_id, [a_id])
        await store.archive_memory(b_id, "keep_archived")

        resolution = await store.resolve(a_id)
        assert resolution["status"] == "archived"
        assert resolution["row"]["id"] == b_id
        assert resolution["chain"] == [a_id]

    async def test_tombstone_with_redirect(self, store: MemoryStore) -> None:
        a_id = await store.add(_entry("A", importance=0.4))
        b_id = await store.add(_entry("B", importance=0.6))
        await store.merge_into_keep(b_id, [a_id])
        # drop 进入归档 → 物删：墓碑带 redirect_to
        drop = await store.get(a_id)
        assert drop is not None
        db = await store._get_db()
        async with store._tx(db):
            await store._archive_entry(drop, "merged_cleanup")
        await db.execute(
            "UPDATE memories_archive SET archived_at_ns=? WHERE id=?",
            (int((time.time() - 200 * 86400) * 1e9), a_id),
        )
        await db.commit()
        purged = await store.purge_archived_memories(older_than_days=90)
        assert purged == 1

        resolution = await store.resolve(a_id)
        assert resolution["status"] == "tombstone"
        assert resolution["row"]["redirect_to"] == b_id

    async def test_missing(self, store: MemoryStore) -> None:
        resolution = await store.resolve(99999)
        assert resolution["status"] == "missing"


class TestAudit:
    async def test_add_records_actor(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("带归因的写入"), actor="tool:memorize")
        audit = await store.list_audit(memory_id=mid)
        assert any(a["action"] == "add" and a["actor"] == "tool:memorize" for a in audit)

    async def test_update_records_old_content_and_actor(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("原始内容"))
        entry = await store.get(mid)
        assert entry is not None
        entry.content = "修订后内容"
        await store.update(entry, actor="tool:update_memory")

        audit = await store.list_audit(memory_id=mid)
        updates = [a for a in audit if a["action"] == "update"]
        assert updates
        assert updates[0]["actor"] == "tool:update_memory"
        assert updates[0]["detail"] == "原始内容"

    async def test_merge_records_actor_both_sides(self, store: MemoryStore) -> None:
        keep_id = await store.add(_entry("存活", importance=0.6))
        drop_id = await store.add(_entry("被并入", importance=0.4))
        await store.merge_into_keep(keep_id, [drop_id], actor="consolidator")

        keep_audit = await store.list_audit(memory_id=keep_id)
        drop_audit = await store.list_audit(memory_id=drop_id)
        assert any(
            a["action"] == "merge" and a["actor"] == "consolidator" for a in keep_audit
        )
        assert any(
            a["action"] == "merge" and a["actor"] == "consolidator"
            and f"#{keep_id}" in a["detail"]
            for a in drop_audit
        )

    async def test_archive_and_restore_actor(self, store: MemoryStore) -> None:
        mid = await store.add(_entry("归档又恢复"))
        await store.archive_memory(mid, "manual_forget", actor="tool:forget")
        await store.restore_memory(mid, actor="tool:restore")
        audit = await store.list_audit(memory_id=mid)
        actions = {a["action"]: a["actor"] for a in audit}
        assert actions.get("archive") == "tool:forget"
        assert actions.get("restore") == "tool:restore"
