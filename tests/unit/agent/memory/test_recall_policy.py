import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.memory.memory_types import MemoryEntry, MemoryType
from agent.memory.probe import deep_probe_hub
from agent.memory.recall_fast import recall_local
from agent.memory.recall_policy import (
    RecallPolicy,
    current_recall_policy,
    recall_options,
    recall_turn,
    set_turn_recall_policy,
)


@pytest.mark.asyncio
async def test_concurrent_turns_isolate_overrides_and_cancel_probe():
    ready = asyncio.Event()
    async def first():
        with recall_options(RecallPolicy(level="off")):
            async with recall_turn("user_qq:1"):
                assert current_recall_policy("user_qq:1").level == "off"
                state = deep_probe_hub.state_for("user_qq:1")
                state.task = asyncio.create_task(asyncio.Event().wait())
                task = state.task
                ready.set()
                await asyncio.sleep(.02)
                assert current_recall_policy("user_qq:1").level == "off"
            assert task.cancelled()
            assert deep_probe_hub.state_for("user_qq:1") is None
    async def second():
        await ready.wait()
        async with recall_turn("user_webui:1", RecallPolicy(level="tags")):
            set_turn_recall_policy(RecallPolicy(level="fast"), "user_webui:1")
            assert current_recall_policy("user_webui:1").level == "fast"
            with pytest.raises(ValueError):
                set_turn_recall_policy(RecallPolicy(), "user_qq:1")
    await asyncio.gather(first(), second())


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["off", "tags"])
async def test_low_latency_modes_never_use_embedding_or_planner(level):
    store = SimpleNamespace(search_by_tags=AsyncMock(return_value=[MemoryEntry(id=42, memory_type=MemoryType.SEMANTIC, content="fact", tags=["project:test"])]), search_unified=AsyncMock())
    retriever = SimpleNamespace(_store=store, recall_limits=lambda: (5, .1),
        _load_entity_profiles=AsyncMock(return_value=[]), _load_permanent_pins=AsyncMock(return_value=[]),
        _format_unified_results=AsyncMock(return_value=[]), _embedder=SimpleNamespace(embed_query=AsyncMock()),
        plan_retrieval=AsyncMock())
    policy = RecallPolicy(level=level, tags=("project:test",) if level == "tags" else ())
    await recall_local(retriever, [], scope="user_qq:1", related_scopes=[], policy=policy)
    retriever._embedder.embed_query.assert_not_awaited()
    retriever.plan_retrieval.assert_not_awaited()
    store.search_unified.assert_not_awaited()
    if level == "tags":
        store.search_by_tags.assert_awaited_once_with(["project:test"], limit=5)
        result = retriever._format_unified_results.call_args.args[0][0]
        assert result.id == "mem:42" and result.tags == ["project:test"]
    else:
        store.search_by_tags.assert_not_awaited()
    await deep_probe_hub.end_reply("user_qq:1")
