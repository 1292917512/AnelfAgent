"""轮内异步召回：本地命中、生命周期、消费会话与动态预算。"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from agent.memory.memory_retriever import MemoryRetriever
from agent.memory.memory_types import MemoryEntry, MemorySearchResult, MemoryType, RetrievalPlan
from agent.memory.probe import DeepProbeHub
from agent.memory.probe_render import RecallBuffer, RecallDelta
from agent.memory.recall_fast import search_local
from agent.memory.recall_policy import RecallPolicy, recall_options, recall_turn, set_turn_recall_policy
from core.conversation_scope import bind_scope, reset_scope


class NullEmbedder:
    async def embed_query(self, query: str):
        return None


@pytest.mark.parametrize("level", ["fast", "deep"])
async def test_new_topic_searches_local_memory_once(store, monkeypatch, level):
    import agent.memory.probe as probe_module

    await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC,
        content="航海项目的发布地点是青岛", tags=["user:qq:42", "topic:航海项目"]))
    retriever = MemoryRetriever(store, NullEmbedder())
    planner = AsyncMock(return_value=RetrievalPlan(queries=["航海项目"], deep_needed=False))
    monkeypatch.setattr(retriever, "plan_retrieval", planner)
    hub = DeepProbeHub()
    monkeypatch.setattr(probe_module, "deep_probe_hub", hub)
    old = [{"role": "user", "content": "先检查服务器磁盘占用情况"}]
    new = [*old, {"role": "user", "content": "航海项目的发布地点在哪里"}]
    with recall_options(RecallPolicy(level=level)):
        async with recall_turn("user_qq:1"):
            state = hub.state_for("user_qq:1")
            hub.prepare("user_qq:1", old, retriever)
            hub.refresh("user_qq:1", old)
            assert state.task is None
            hub.refresh("user_qq:1", new)
            first = state.task
            assert first is not None
            hub.refresh("user_qq:1", new)
            assert state.task is first
            await first
            assert "青岛" in state.rendered
            assert "[channel:qq]" in state.rendered and "[uid:42]" in state.rendered
            hub.refresh("user_qq:1", new)
            assert state.task is first
            assert planner.await_count == (1 if level == "deep" else 0)
        assert hub.state_for("user_qq:1") is None


def test_cumulative_budget_only_records_presented_facts():
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:1")
    state.buffer = RecallBuffer(400)
    hub._publish(state, 0, [RecallDelta("补充记忆", "首个事实", "mem:1", "首个事实")])
    first = state.rendered
    hub._publish(state, 0, [RecallDelta("补充记忆", "第二个事实", "mem:2", "第二个事实")])
    assert state.rendered.startswith(first)
    hub._publish(state, 0, [RecallDelta("图谱综合", "长" * 900, "mem:3", "长" * 900)])
    assert "首个事实" in state.rendered and "第二个事实" in state.rendered
    assert len(state.rendered) <= 400
    assert not state.ledger.seen_result("mem:3")
    hub._publish(state, 0, [RecallDelta("补充记忆", "可容纳的小事实", "mem:4", "小事实")])
    assert state.ledger.seen_result("mem:4")
    assert len(state.rendered) <= 400


async def test_policy_change_rejects_late_result_and_keeps_presented_context(store, monkeypatch):
    import agent.memory.probe as probe_module

    hub = DeepProbeHub()
    monkeypatch.setattr(probe_module, "deep_probe_hub", hub)
    entered = asyncio.Event()

    async def slow_cognee(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return [RecallDelta("深层记忆", "已经过期的结果", "mem:9", "过期结果")]

    monkeypatch.setattr(hub, "_collect_cognee", slow_cognee)
    async with recall_turn("user_qq:1"):
        state = hub.state_for("user_qq:1")
        hub._publish(state, 0, [RecallDelta("补充记忆", "已经展示的事实", "mem:1", "已展示")])
        previous = state.rendered
        hub.start(scope=state.scope, plan=RetrievalPlan(queries=["q"], deep_needed=True), store=store)
        await entered.wait()
        set_turn_recall_policy(RecallPolicy(level="off"), state.scope)
        await asyncio.gather(state.task, return_exceptions=True)
        assert state.rendered == previous
        assert not state.ledger.seen_result("mem:9")


async def test_old_turn_cleanup_cannot_remove_replacement():
    hub = DeepProbeHub(ttl_seconds=0)
    old = hub.begin_reply("user_qq:1", managed=True)
    hub.begin_reply("user_webui:2", managed=True)
    assert hub.state_for(old.scope) is old
    new = hub.begin_reply(old.scope, managed=True)
    await hub.end_reply(old.scope, expected=old)
    assert hub.state_for(old.scope) is new
    assert not hub._publish(old, old.revision, [RecallDelta("旧轮次", "过期结果")])
    assert not new.rendered
    await hub.end_reply(new.scope, expected=new)


async def test_manual_recall_records_consumer_not_searched_identity(store, monkeypatch):
    import agent.memory.tools as mem_tools

    hub = DeepProbeHub()
    monkeypatch.setattr(mem_tools, "deep_probe_hub", hub)
    monkeypatch.setattr(mem_tools, "_deps", lambda: mem_tools.MemoryToolDeps(store, None))
    await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC,
        content="小王的项目在青岛", tags=["user:qq:42"]))
    consumer = hub.begin_reply("user_webui:1#chat")
    other = hub.begin_reply("user_qq:42")
    token = bind_scope(consumer.scope)
    try:
        await mem_tools.recall("小王的项目", tags="user:qq:42")
    finally:
        reset_scope(token)
    assert consumer.ledger.content_keys
    assert not other.ledger.content_keys


async def test_results_returning_after_manual_recall_are_deduplicated(store):
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:1")
    result = MemorySearchResult(id="mem:1", snippet="同一事实", score=1)
    deltas = await hub._memory_deltas([result], store, "补充记忆")
    hub.record(state.scope, results=[result])
    hub._publish(state, 0, deltas)
    assert not state.rendered


async def test_embedding_failure_keeps_local_tag_filter(store, monkeypatch):
    from types import SimpleNamespace

    await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC,
        content="航海项目将在青岛发布", tags=["topic:航海项目"]))
    await store.add(MemoryEntry(memory_type=MemoryType.SEMANTIC,
        content="另一个航海项目将在上海发布", tags=["topic:另一项目"]))
    retriever = MemoryRetriever(store, SimpleNamespace(embed_query=AsyncMock(side_effect=TimeoutError)))
    results = await search_local(retriever, "航海项目发布", tags=("topic:航海项目",))
    assert results
    assert all("topic:航海项目" in item.tags for item in results)


@pytest.mark.parametrize("policy", [
    RecallPolicy(level="off"), RecallPolicy(level="tags"),
    RecallPolicy(level="fast", tags=("topic:限定范围",)), RecallPolicy(background=False),
])
async def test_restricted_modes_do_not_spawn_background_work(store, monkeypatch, policy):
    import agent.memory.probe as probe_module

    hub = DeepProbeHub()
    monkeypatch.setattr(probe_module, "deep_probe_hub", hub)
    async with recall_turn("user_qq:1", policy):
        state = hub.state_for("user_qq:1")
        hub.prepare(state.scope, [], MemoryRetriever(store, NullEmbedder()))
        hub.refresh(state.scope, [{"role": "user", "content": "需要调查之前的航海项目发布情况"}])
        assert state.task is None


async def test_reapplying_policy_keeps_existing_probe_and_query_budget(monkeypatch):
    import agent.memory.probe as probe_module

    hub = DeepProbeHub()
    monkeypatch.setattr(probe_module, "deep_probe_hub", hub)
    policy = RecallPolicy(level="fast")
    async with recall_turn("user_qq:1", policy):
        state = hub.state_for("user_qq:1")
        state.queries.add("当前话题")
        state.task = asyncio.create_task(asyncio.Event().wait())
        set_turn_recall_policy(policy, state.scope)
        await asyncio.sleep(0)
        assert not state.task.done()
        assert state.queries == {"当前话题"}
