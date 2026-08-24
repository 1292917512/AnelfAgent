"""recall 工具增强单元测试：source 标志 / filter_tags 硬过滤 / 深浅召回 / merged 排除。

注意：FTS5 unicode61 将连续中文视为单 token，测试内容统一用空格分隔的
锚点关键词（如 "catnote ..."）保证 FTS 可命中。
"""

from __future__ import annotations

import json

import pytest

from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemoryType


def _entry(content: str, tags: list[str], importance: float = 0.6) -> MemoryEntry:
    return MemoryEntry(
        memory_type=MemoryType.SEMANTIC, content=content,
        tags=tags, importance=importance,
    )


@pytest.fixture
def bound_store(store: MemoryStore):
    from agent.memory import tools as mem_tools

    mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
    yield mem_tools
    mem_tools.memory_tools_port.unbind()


class TestRecallSourceMarking:
    async def test_results_carry_source_and_time(self, bound_store, store: MemoryStore) -> None:
        await store.add(_entry("catnote 主人喜欢猫", ["user:1", "topic:猫"]))
        result = json.loads(await bound_store.recall("catnote"))
        assert result["count"] >= 1
        item = result["results"][0]
        assert item["source"] == "memory"
        assert "time" in item  # memory 来源带写入日期

    async def test_depth_field_echoed(self, bound_store, store: MemoryStore) -> None:
        await store.add(_entry("memoryanchor 记忆内容", ["topic:x"]))
        shallow = json.loads(await bound_store.recall("memoryanchor"))
        assert shallow["depth"] == "shallow"
        deep = json.loads(await bound_store.recall("memoryanchor", depth="deep"))
        assert deep["depth"] == "deep"


class TestFilterTags:
    async def test_hard_filter_excludes_mismatched(self, bound_store, store: MemoryStore) -> None:
        await store.add(_entry("catnote 猫的记忆", ["user:1", "topic:猫"]))
        await store.add(_entry("catnote 另一条猫记忆", ["user:2", "topic:猫"]))
        result = json.loads(await bound_store.recall("catnote", filter_tags="user:1"))
        contents = [r["content"] for r in result["results"]]
        assert any("猫的记忆" in c for c in contents)
        assert not any("另一条" in c for c in contents)

    async def test_soft_tags_do_not_filter(self, bound_store, store: MemoryStore) -> None:
        await store.add(_entry("catnote 猫的记忆甲", ["user:1", "topic:猫"]))
        await store.add(_entry("catnote 猫的记忆乙", ["user:2", "topic:猫"]))
        result = json.loads(await bound_store.recall("catnote", tags="user:1", limit=10))
        # tags 仅加权不过滤：两条都应在结果中
        assert result["count"] == 2


class TestMergedExclusion:
    async def test_zero_importance_excluded_from_fts(self, store: MemoryStore) -> None:
        await store.add(_entry("uniqueanchor 有效记忆", ["topic:a"]))
        await store.add(_entry("uniqueanchor 已合并记忆", ["topic:b"], importance=0.0))
        results = await store.search_fts("uniqueanchor")
        contents = [e.content for e, _ in results]
        assert any("有效记忆" in c for c in contents)
        assert not any("已合并" in c for c in contents)

    async def test_zero_importance_excluded_from_tag_search(self, store: MemoryStore) -> None:
        await store.add(_entry("有效", ["topic:c"]))
        await store.add(_entry("已合并", ["topic:c"], importance=0.0))
        results = await store.search_by_tags(["topic:c"])
        assert [e.content for e in results] == ["有效"]

    async def test_zero_importance_excluded_from_hybrid(self, store: MemoryStore) -> None:
        await store.add(_entry("hybridanchor 混合检索有效", ["topic:d"]))
        await store.add(_entry("hybridanchor 混合检索合并", ["topic:d"], importance=0.0))
        results = await store.search_hybrid("hybridanchor", query_tags=["topic:d"])
        contents = [e.content for e, _ in results]
        assert any("有效" in c for c in contents)
        assert not any("合并" in c for c in contents)


class TestDeepAssociations:
    async def test_two_hop_association_chain(self, bound_store, store: MemoryStore) -> None:
        """A -(topic:x)-> B -(topic:y)-> C：深度召回应沿标签关联想出 C。

        标签智能（共现/图谱邻居）扩展种子后，C 可能在一跳即被共现种子命中
        （不带 hop=2 标记）——新契约只断言 C 出现在关联结果中，不断言跳数归属。
        """
        await store.add(_entry("startanchor 起点记忆", ["user:1", "topic:x"]))
        await store.add(_entry("一跳关联", ["topic:x", "topic:y"]))
        await store.add(_entry("二跳关联", ["topic:y"]))

        result = json.loads(await bound_store.recall("startanchor", depth="deep"))
        related = result["related"]
        assert any("二跳关联" in r["content"] for r in related)

    async def test_shallow_association_expands_via_tag_intel(self, bound_store, store: MemoryStore) -> None:
        """浅召回经标签共现扩展种子：topic:y 与 topic:x 共现 → 只带 y 的记忆可被联想。"""
        await store.add(_entry("startanchor 起点记忆", ["user:1", "topic:x"]))
        await store.add(_entry("一跳关联", ["topic:x", "topic:y"]))
        await store.add(_entry("共现关联", ["topic:y"]))

        result = json.loads(await bound_store.recall("startanchor"))
        contents = [r["content"] for r in result["related"]]
        assert any("一跳关联" in c for c in contents)
        assert any("共现关联" in c for c in contents)
