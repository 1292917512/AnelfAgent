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
    async def test_reflection_updates_hub_and_memory_without_losing_origin(self, bound_store, store) -> None:
        from core.conversation_scope import bind_scope, reset_scope

        ids = []
        scopes = ["user_webui:web_user", "reflect:de8b4383", "reflect:a1234567"]
        for index, scope in enumerate(scopes):
            token = bind_scope(scope)
            try:
                if index < 2:
                    result = json.loads(await bound_store.memorize(
                        f"hubanchor 标签索引 {index}", tags="type:permanent,main:hub", importance=1,
                    ))
                else:
                    result = json.loads(await bound_store.update_memory(ids[0], content="hubanchor 整理完成"))
            finally:
                reset_scope(token)
            assert result.get("ok"), result
            ids.append(result["id"])

        assert len(set(ids)) == 1
        entry = await store.get(ids[0])
        assert entry.content == "hubanchor 整理完成"
        assert entry.tags == ["type:permanent", "main:hub"]
        assert entry.importance == 1
        expected = [{"kind": "agent_note", "scope": scope} for scope in scopes]
        assert entry.metadata["origins"] == expected
        detail = json.loads(await bound_store.get_memory(ids[0]))
        assert detail["provenance"]["origins"] == expected
        recalled = json.loads(await bound_store.recall("hubanchor", min_score=0))
        item = next(r for r in recalled["results"] if r["id"] == f"mem:{ids[0]}")
        assert item["provenance"]["origins"] == expected

    @pytest.mark.parametrize("scope", ["_global", "reflect:de8b4383", "reflect:da09792e"])
    async def test_internal_note_creation_keeps_execution_scope(self, bound_store, store, scope) -> None:
        from core.config import ConfigManager
        from core.conversation_scope import bind_scope, reset_scope

        ConfigManager.set("memory_llm_dedup_enabled", False)
        token = bind_scope(scope)
        try:
            saved = json.loads(await bound_store.memorize("noteanchor 整理中发现的事实", tags="topic:整理"))
        finally:
            reset_scope(token)
        assert saved.get("ok"), saved
        entry = await store.get(saved["id"])
        assert entry.tags == ["topic:整理"]
        assert entry.metadata["origins"] == [{"kind": "agent_note", "scope": scope}]

    async def test_duplicate_preserves_only_selected_origin_without_reinforcement(self, store) -> None:
        from agent.memory.dedup import retain_duplicate_origin
        from agent.memory.provenance import note_origin

        entries = [_entry("喜欢安静的房间", ["user:qq:1"]), _entry("喜欢在房间听音乐", ["user:qq:1"])]
        for entry in entries:
            entry.id = await store.add(entry)
        metadata = note_origin("user_qq:1")
        metadata.update({"activity_date": "2026-10-20", "sensitivity": "private"})
        assert await retain_duplicate_origin(store, entries[0].id, metadata)
        assert await retain_duplicate_origin(store, entries[0].id, metadata)
        recorded = await store.get(entries[0].id)
        assert recorded.metadata["origins"] == metadata["origins"]
        assert "evidence" not in recorded.metadata
        assert "activity_date" not in recorded.metadata
        assert recorded.metadata["sensitivity"] == "private"
        assert (await store.get(entries[1].id)).metadata == {}

    async def test_tool_write_read_recall_and_archive_keep_origin(self, bound_store, store) -> None:
        from core.config import ConfigManager
        from core.conversation_scope import bind_scope, reset_scope

        ConfigManager.set("memory_llm_dedup_enabled", False)
        token = bind_scope("group_qq:source")
        try:
            saved = json.loads(await bound_store.memorize("originanchor 乙喜欢安静的房间", tags="user:telegram:2",
                                                         sensitivity="private", temporal_scope="state"))
        finally:
            reset_scope(token)
        assert saved["ok"]
        mid = saved["id"]
        detail = json.loads(await bound_store.get_memory(mid))
        origin = detail["provenance"]["origins"][0]
        assert origin == {"kind": "agent_note", "scope": "group_qq:source"}
        assert detail["tags"] == ["user:telegram:2"]
        recalled = json.loads(await bound_store.recall("originanchor", min_score=0))
        item = next(r for r in recalled["results"] if r["id"] == f"mem:{mid}")
        assert item["provenance"]["origins"] == [origin] and item["sensitivity"] == "private"
        await store.archive_memory(mid, reason="test")
        archived = json.loads(await bound_store.get_memory(mid))
        assert archived["provenance"]["origins"] == [origin]
        assert archived["sensitivity"] == "private" and archived["timestamp"] == detail["timestamp"]
        forgotten = json.loads(await bound_store.recall("originanchor", min_score=0))["forgotten"][0]
        assert forgotten["provenance"]["origins"] == [origin]
        assert forgotten["sensitivity"] == "private"

    async def test_same_content_for_different_people_is_not_write_duplicate(self, store) -> None:
        from agent.memory.dedup import gather_dedup_candidates

        first = _entry("sameperson 喜欢安静的房间", ["user:qq:1"])
        mid = await store.add(first)
        first = await store.get(mid)
        assert first is not None
        # 规则判重保持保守：不同人的同句文本不做零成本拦截
        assert await store.has_similar_content(first.content, tags=["user:qq:1"])
        assert not await store.has_similar_content(first.content, tags=["user:qq:2"])
        # 候选召回不做身份预过滤：跨群/跨人的候选交给判断段结合标签裁决
        candidates = await gather_dedup_candidates(store, None, first.content)
        assert any(c.id == mid for c in candidates)

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


class TestForgottenFallback:
    async def test_archived_surfaces_when_main_empty(self, bound_store, store: MemoryStore) -> None:
        mid = await store.add(_entry("goneanchor 消失的记忆", ["topic:gone"]))
        assert await store.archive_memory(mid)

        result = json.loads(await bound_store.recall("goneanchor"))
        assert result["count"] == 0
        assert len(result["forgotten"]) == 1
        item = result["forgotten"][0]
        assert item["kind"] == "archived"
        assert item["id"] == mid
        assert item["restorable"] is True
        assert "消失的记忆" in item["content"]
        assert "forgotten_hint" in result

    async def test_weak_archived_hidden_when_main_has_results(
        self, bound_store, store: MemoryStore,
    ) -> None:
        await store.add(_entry("sharedanchor 活跃的记忆", ["topic:a"]))
        archived_id = await store.add(_entry("sharedanchor 归档的记忆", ["topic:b"]))
        assert await store.archive_memory(archived_id)

        result = json.loads(await bound_store.recall("sharedanchor"))
        assert result["count"] >= 1
        # 主检索有结果时，关键词弱命中（0.35 < 0.5）的归档记忆不浮现
        assert "forgotten" not in result

    async def test_restore_memory_tool(self, bound_store, store: MemoryStore) -> None:
        mid = await store.add(_entry("backanchor 恢复的记忆", ["topic:back"]))
        assert await store.archive_memory(mid)

        result = json.loads(await bound_store.restore_memory(mid))
        assert result["ok"] is True
        restored = await store.get(mid)
        assert restored is not None
        assert restored.content.startswith("backanchor")
        # 恢复后重新参与常规召回
        recalled = json.loads(await bound_store.recall("backanchor"))
        assert recalled["count"] >= 1
        assert "forgotten" not in recalled

    async def test_restore_missing_returns_not_found(self, bound_store) -> None:
        result = json.loads(await bound_store.restore_memory(999999))
        assert result["cause"] == "not_found"
        assert result["retryable"] is False

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
