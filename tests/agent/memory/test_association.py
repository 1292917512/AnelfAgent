"""记忆关联网络（search_associative + 上下文加权 + 关联扩展）单元测试。"""

from __future__ import annotations

import json

import pytest

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemorySearchResult, MemoryType


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(str(tmp_path / "memory.sqlite3"))
    yield s
    await s.close()


def _entry(content: str, tags: list[str], importance: float = 0.6) -> MemoryEntry:
    return MemoryEntry(
        memory_type=MemoryType.SEMANTIC, content=content,
        tags=tags, importance=importance,
    )


class TestSearchAssociative:
    async def test_any_tag_match(self, store: MemoryStore) -> None:
        await store.add(_entry("小A喜欢猫", ["user:111", "topic:猫"]))
        await store.add(_entry("小A爱吃鱼", ["user:111", "topic:鱼"]))
        await store.add(_entry("无关记忆", ["user:999"]))
        results = await store.search_associative(["user:111", "topic:猫"])
        contents = [e.content for e, _ in results]
        assert "小A喜欢猫" in contents
        assert "小A爱吃鱼" in contents  # user:111 命中
        assert "无关记忆" not in contents

    async def test_exclude_ids(self, store: MemoryStore) -> None:
        id1 = await store.add(_entry("记忆1", ["user:111"]))
        await store.add(_entry("记忆2", ["user:111"]))
        results = await store.search_associative(["user:111"], exclude_ids={id1})
        assert all(e.id != id1 for e, _ in results)

    async def test_score_ordering(self, store: MemoryStore) -> None:
        await store.add(_entry("单标签命中", ["user:111"]))
        await store.add(_entry("双标签命中", ["user:111", "topic:猫"]))
        results = await store.search_associative(["user:111", "topic:猫"])
        assert results[0][0].content == "双标签命中"

    async def test_empty_tags(self, store: MemoryStore) -> None:
        assert await store.search_associative([]) == []


class TestScopeBoost:
    def test_boosts_current_scope_memories(self) -> None:
        results = [
            MemorySearchResult(id="mem:1", snippet="a", score=0.5, tags=["user:123"]),
            MemorySearchResult(id="mem:2", snippet="b", score=0.5, tags=["topic:x"]),
        ]
        boosted = MemoryRetriever._apply_scope_boost(results, ["user_123"])
        assert boosted[0].id == "mem:1"
        assert boosted[0].score > 0.5

    def test_no_scopes_no_change(self) -> None:
        results = [MemorySearchResult(id="mem:1", snippet="a", score=0.5, tags=[])]
        boosted = MemoryRetriever._apply_scope_boost(results, [])
        assert boosted[0].score == 0.5

    def test_group_scope(self) -> None:
        results = [
            MemorySearchResult(id="mem:1", snippet="a", score=0.5, tags=["group:456"]),
        ]
        boosted = MemoryRetriever._apply_scope_boost(results, ["group_456"])
        assert boosted[0].score > 0.5


class TestExpandAssociations:
    async def test_expands_related_memories(self, store: MemoryStore) -> None:
        from agent.memory.embedder import Embedder
        main_id = await store.add(_entry("主结果", ["user:111", "topic:猫"]))
        await store.add(_entry("关联记忆", ["user:111", "topic:狗"]))

        retriever = MemoryRetriever(store, Embedder())
        results = [MemorySearchResult(
            id=f"mem:{main_id}", snippet="主结果", score=0.9,
            tags=["user:111", "topic:猫"],
        )]
        expanded = await retriever._expand_associations(results, limit=5)
        assert len(expanded) == 2
        assoc = expanded[-1]
        assert assoc.snippet == "关联记忆"
        assert assoc.provenance.get("associated") is True
        assert assoc.score < 0.9  # 关联打折

    async def test_no_tags_no_expansion(self, store: MemoryStore) -> None:
        from agent.memory.embedder import Embedder
        retriever = MemoryRetriever(store, Embedder())
        results = [MemorySearchResult(id="mem:1", snippet="a", score=0.9, tags=[])]
        expanded = await retriever._expand_associations(results, limit=5)
        assert len(expanded) == 1


class TestMemorizeAutoTag:
    async def test_auto_tag_current_scope(self, store: MemoryStore, monkeypatch) -> None:
        from agent.memory import tools as mem_tools
        from agent.mind.tool_activation import bind_scope, reset_scope

        monkeypatch.setattr(mem_tools, "_store", store)
        monkeypatch.setattr(mem_tools, "_embedder", None)

        token = bind_scope("user_424242")
        try:
            result = json.loads(await mem_tools.memorize("测试自动标签"))
            assert result["ok"] is True
            assert "user:424242" in result["tags"]
        finally:
            reset_scope(token)

    async def test_explicit_tag_not_overridden(self, store: MemoryStore, monkeypatch) -> None:
        from agent.memory import tools as mem_tools
        from agent.mind.tool_activation import bind_scope, reset_scope

        monkeypatch.setattr(mem_tools, "_store", store)
        monkeypatch.setattr(mem_tools, "_embedder", None)

        token = bind_scope("user_424242")
        try:
            result = json.loads(await mem_tools.memorize("显式标签", tags="user:111"))
            assert "user:111" in result["tags"]
            assert "user:424242" not in result["tags"]
        finally:
            reset_scope(token)
