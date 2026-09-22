"""标签归并候选（tag_intel.merge_candidates）测试：写法变体与包含关系对的确定性生产。"""

from __future__ import annotations

import time

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType


async def _add_with_tags(store: MemoryStore, content: str, tags: list[str]) -> int:
    return await store.add(MemoryEntry(
        memory_type=MemoryType.SEMANTIC,
        content=content,
        importance=0.6,
        timestamp=time.time(),
        tags=tags,
    ))


class TestTagMergeCandidates:
    async def test_form_variant_detected(self, store: MemoryStore) -> None:
        await _add_with_tags(store, "记忆1", ["topic:火锅"])
        await _add_with_tags(store, "记忆2", ["topic:火锅"])
        await _add_with_tags(store, "记忆3", ["topic:火锅 "])  # 尾随空白孪生

        candidates = await store.tag_merge_candidates()
        variants = [c for c in candidates if c["reason"] == "写法变体"]
        assert len(variants) == 1
        assert variants[0]["into"] == "topic:火锅"  # 高频形态为归并目标
        assert variants[0]["from"] == "topic:火锅 "

    async def test_fullwidth_variant_detected(self, store: MemoryStore) -> None:
        await _add_with_tags(store, "记忆1", ["topic:火锅"])
        await _add_with_tags(store, "记忆2", ["topic:火锅"])
        await _add_with_tags(store, "记忆3", ["topic:火锅　"])  # 全角空格孪生

        candidates = await store.tag_merge_candidates()
        assert any(c["reason"] == "写法变体" for c in candidates)

    async def test_containment_pair_detected(self, store: MemoryStore) -> None:
        for i in range(3):
            await _add_with_tags(store, f"火锅记忆{i}", ["topic:火锅"])
        for i in range(2):
            await _add_with_tags(store, f"蘸料记忆{i}", ["topic:火锅蘸料"])

        candidates = await store.tag_merge_candidates()
        pairs = [c for c in candidates if c["reason"] == "名称包含关系"]
        assert len(pairs) == 1
        assert pairs[0]["from"] == "topic:火锅蘸料"  # 低频并入高频
        assert pairs[0]["into"] == "topic:火锅"

    async def test_single_occurrence_topics_excluded(self, store: MemoryStore) -> None:
        await _add_with_tags(store, "孤独记忆", ["topic:孤独"])
        await _add_with_tags(store, "孤独症记忆", ["topic:孤独症"])

        candidates = await store.tag_merge_candidates()
        assert not any(c["reason"] == "名称包含关系" for c in candidates)

    async def test_no_candidates_when_healthy(self, store: MemoryStore) -> None:
        await _add_with_tags(store, "记忆1", ["topic:火锅"])
        await _add_with_tags(store, "记忆2", ["topic:烧烤"])

        assert await store.tag_merge_candidates() == []

    async def test_merged_zombie_tags_not_counted(self, store: MemoryStore) -> None:
        # importance=0 的僵尸条目不参与标签统计（tag_intel 扫描带 importance>0 过滤）
        zombie_id = await _add_with_tags(store, "僵尸", ["topic:旧火锅"])
        entry = await store.get(zombie_id)
        assert entry is not None
        entry.importance = 0
        await store.update(entry)

        candidates = await store.tag_merge_candidates()
        assert not any("旧火锅" in c["from"] for c in candidates)
