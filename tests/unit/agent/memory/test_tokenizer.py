"""FTS 分词器与 jieba 索引链路单元测试。"""

from __future__ import annotations

import pytest

from agent.memory.store.tokenizer import (
    add_words,
    available,
    tokenize_for_index,
    tokenize_for_query,
)

pytestmark = pytest.mark.skipif(not available(), reason="jieba 未安装")


class TestTokenizer:
    def test_index_tokenizes_chinese_words(self) -> None:
        tokens = tokenize_for_index("主人上周搬到了新家")
        assert "搬到" in tokens
        assert "新家" in tokens

    def test_query_covers_compound_words(self) -> None:
        # 精确模式可能切碎复合词，查询取并集保证长词可查
        tokens = tokenize_for_query("他搬到哪里了")
        assert "哪里" in tokens

    def test_english_passthrough(self) -> None:
        assert "cats" in tokenize_for_query("cats and coffee")

    def test_punctuation_filtered(self) -> None:
        tokens = tokenize_for_index("你好！！！？？")
        assert "！" not in tokens and "？" not in tokens

    def test_empty_input(self) -> None:
        assert tokenize_for_index("") == ""
        assert tokenize_for_query("  ") == []

    def test_custom_words(self) -> None:
        add_words(["安尔芙测试词"])
        assert "安尔芙测试词" in tokenize_for_index("这是安尔芙测试词的内容")


class TestFtsWithJieba:
    async def test_chinese_fts_hits(self, tmp_path) -> None:
        """回归：unicode61 整句 token 时代 bigram/词级查询不命中的问题。"""
        from agent.memory.memory_store import MemoryStore
        from agent.memory.memory_types import MemoryEntry, MemoryType

        store = MemoryStore(str(tmp_path / "m.db"))
        try:
            await store.add(MemoryEntry(
                memory_type=MemoryType.EPISODIC,
                content="主人上周搬到了新家，离公司更近了",
            ))
            hits = await store.search_fts("搬到", limit=5)
            assert len(hits) == 1
            hits = await store.search_fts("新家", limit=5)
            assert len(hits) == 1
        finally:
            await store.close()

    async def test_fts_update_and_delete_sync(self, tmp_path) -> None:
        from agent.memory.memory_store import MemoryStore
        from agent.memory.memory_types import MemoryEntry, MemoryType

        store = MemoryStore(str(tmp_path / "m.db"))
        try:
            mid = await store.add(MemoryEntry(
                memory_type=MemoryType.SEMANTIC, content="旧内容关于苹果",
            ))
            entry = await store.get(mid)
            assert entry is not None
            entry.content = "新内容关于香蕉"
            await store.update(entry, clear_embedding=True)
            assert await store.search_fts("香蕉", limit=5)
            assert not await store.search_fts("苹果", limit=5)
            await store.delete(mid)
            assert not await store.search_fts("香蕉", limit=5)
        finally:
            await store.close()

    async def test_version_increments_on_update(self, tmp_path) -> None:
        from agent.memory.memory_store import MemoryStore
        from agent.memory.memory_types import MemoryEntry, MemoryType

        store = MemoryStore(str(tmp_path / "m.db"))
        try:
            mid = await store.add(MemoryEntry(
                memory_type=MemoryType.SEMANTIC, content="初始内容",
            ))
            entry = await store.get(mid)
            assert entry is not None and entry.version == 1
            entry.content = "修订内容"
            await store.update(entry, clear_embedding=True)
            entry2 = await store.get(mid)
            assert entry2 is not None and entry2.version == 2
        finally:
            await store.close()
