"""主标签记忆（main:hub）单元测试：骨架自愈 / 注入渲染 / pins 排除 / 工具侧守卫。"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from agent.memory import hub
from agent.memory.memory_store import MemoryStore
from agent.memory.memory_types import MemoryEntry, MemorySearchResult, MemoryType


@pytest.fixture
def bound_store(store: MemoryStore):
    from agent.memory import tools as mem_tools

    mem_tools.memory_tools_port.set(mem_tools.MemoryToolDeps(store, None))
    yield mem_tools
    mem_tools.memory_tools_port.unbind()


class TestEnsureHub:
    async def test_creates_skeleton(self, store: MemoryStore) -> None:
        assert await hub.ensure_hub(store) is True
        entry = await hub.get_hub_entry(store)
        assert entry is not None
        assert entry.memory_type == MemoryType.PERMANENT
        assert hub.HUB_TAG in entry.tags
        assert entry.importance == 1.0
        assert "## 标签索引" in entry.content
        assert "## 即时记录" in entry.content

    async def test_idempotent(self, store: MemoryStore) -> None:
        assert await hub.ensure_hub(store) is True
        assert await hub.ensure_hub(store) is False
        entries = await store.search_by_tags([hub.HUB_TAG], limit=5)
        assert len(entries) == 1


class TestLoadHubBlock:
    async def test_missing_returns_empty(self, store: MemoryStore) -> None:
        assert await hub.load_hub_block(store) == ""

    async def test_renders_header_and_content(self, store: MemoryStore) -> None:
        await hub.ensure_hub(store)
        block = await hub.load_hub_block(store)
        assert block.startswith("[主标签记忆]")
        assert "## 标签索引" in block

    async def test_budget_preserves_index_and_recent_work(self, store: MemoryStore) -> None:
        from core.config import ConfigManager
        ConfigManager.set("memory_hub_inject_max_chars", 200)
        await store.add(MemoryEntry(memory_type=MemoryType.PERMANENT,
            content="## 标签索引\n资产入口" + "长期条目" * 200 + "\n## 即时记录\n等待扫码后再发探针",
            tags=hub.HUB_TAGS))
        block = await hub.load_hub_block(store)
        assert "超出注入预算已截断" in block
        assert "## 标签索引" in block and "等待扫码后再发探针" in block
        assert len(block) <= 200 + len(hub._INJECT_HEADER) + 1

    async def test_provider_reads_latest_hub_on_each_round(self, store, monkeypatch):
        from agent.context.providers import collect_provider_messages
        from core.context_provider import ContextProviderRegistry
        monkeypatch.setattr(ContextProviderRegistry, "_providers", {})
        hub.register_provider(store)
        await hub.ensure_hub(store)
        first = await collect_provider_messages(None, "user_qq:1")
        entry = await hub.get_hub_entry(store)
        entry.content = "## 标签索引\n工程入口\n## 即时记录\n等待扫码"
        await store.update(entry)
        second = await collect_provider_messages(None, "user_qq:1")
        assert first != second
        assert len(second) == 1 and "等待扫码" in second[0]["content"]
        assert second[0]["_layer"] == "provider"
        assert second[0]["_source"]["provider"] == "memory_hub"
        assert await collect_provider_messages(None, "user_qq:1") == second


class TestPinsExclusion:
    async def test_hub_not_in_permanent_pins(self, store: MemoryStore) -> None:
        from agent.memory.memory_retriever import MemoryRetriever

        retriever = MemoryRetriever(store, Mock())
        await hub.ensure_hub(store)
        await store.add(MemoryEntry(
            memory_type=MemoryType.PERMANENT, content="pinrule 核心规则",
            tags=["type:permanent", "topic:规则"], importance=1.0,
        ))
        pins = await retriever._load_permanent_pins([])
        assert pins, "普通永久记忆应出现在 pins 中"
        assert all(hub.HUB_TAG not in (p.tags or []) for p in pins)
        assert any("pinrule" in p.snippet for p in pins)

    async def test_hub_excluded_from_automatic_recall_but_readable(
        self, store: MemoryStore,
    ) -> None:
        from agent.memory.memory_retriever import MemoryRetriever
        from agent.memory.probe import DeepProbeHub

        retriever = MemoryRetriever(store, Mock())
        entry = MemoryEntry(
            memory_type=MemoryType.PERMANENT, content="过期工作窗口",
            tags=[*hub.HUB_TAGS, "user:qq:1"],
        )
        entry.id = await store.add(entry)
        rule = MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="操作前先查询密码本",
            tags=["user:qq:1"],
        )
        rule.id = await store.add(rule)
        results = [MemorySearchResult.from_entry(item, 1) for item in (entry, rule)]

        injected = [
            await retriever._load_entity_profiles(["user_qq:1"]),
            await retriever._format_unified_results(results),
            await retriever._fallback_recent(5),
        ]
        for messages in injected:
            text = json.dumps(messages, ensure_ascii=False)
            assert "操作前先查询密码本" in text
            assert "过期工作窗口" not in text
        deltas = await DeepProbeHub._memory_deltas(results, store, "记忆")
        assert len(deltas) == 1 and deltas[0].result_id == f"mem:{rule.id}"
        assert "过期工作窗口" in await hub.load_hub_block(store)
        assert (await store.search_by_tags([hub.HUB_TAG]))[0].id == entry.id


class TestHubToolGuards:
    async def test_upsert_matches_by_hub_tag_only(
        self, bound_store, store: MemoryStore,
    ) -> None:
        """附带额外标签覆写仍匹配同一 hub 条目（不会创建重复 hub）。"""
        r1 = json.loads(await bound_store.memorize(
            "v1 内容", tags="type:permanent,main:hub,topic:工作",
        ))
        assert r1["verdict"] == "stored"
        r2 = json.loads(await bound_store.memorize(
            "v2 内容", tags="type:permanent,main:hub",
        ))
        assert r2["verdict"] == "updated"
        entries = await store.search_by_tags([hub.HUB_TAG], limit=5)
        assert len(entries) == 1
        assert entries[0].content == "v2 内容"

    async def test_forget_hub_rejected(self, bound_store, store: MemoryStore) -> None:
        await bound_store.memorize("hub 内容", tags="type:permanent,main:hub")
        entry = await hub.get_hub_entry(store)
        assert entry is not None and entry.id is not None
        result = json.loads(await bound_store.forget(entry.id))
        assert "error" in result
        assert "禁止归档" in result["error"]
        assert await hub.get_hub_entry(store) is not None  # 仍活跃

    async def test_forget_normal_memory_unaffected(
        self, bound_store, store: MemoryStore,
    ) -> None:
        mid = await store.add(MemoryEntry(
            memory_type=MemoryType.SEMANTIC, content="普通事实",
            tags=["type:fact"], importance=0.6,
        ))
        result = json.loads(await bound_store.forget(mid))
        assert result["ok"] is True
