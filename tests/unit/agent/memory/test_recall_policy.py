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
    default_recall_policy,
    recall_options,
    recall_turn,
    set_turn_recall_policy,
)


@pytest.mark.parametrize(("history", "relations", "expected"), [
    (.04, .04, "off"), (.04, .15, "off"), (.96, .04, "fast"), (.96, .96, "deep"),
    (.4, .04, "fast"), (.96, .4, "deep"), (.04, .96, "deep"),
])
async def test_semantic_routing_only_reduces_recall_at_high_confidence(monkeypatch, history, relations, expected):
    from agent.judgment import JudgmentReport, JudgmentSource, NoulAnswer
    from agent.memory import recall_routing as routing

    report = JudgmentReport(source=JudgmentSource.TYPESAFE,
                           answers={"history": NoulAnswer(noul=history), "relations": NoulAnswer(noul=relations)})
    judge = AsyncMock(return_value=report)
    monkeypatch.setattr(routing, "load_config", lambda: SimpleNamespace(
        enabled=True, native_configured=True, timeout=10, model="decision-model"))
    monkeypatch.setattr(routing, "get_judgment_engine", lambda: SimpleNamespace(judge=judge))
    async with recall_turn("user_webui:1"):
        policy, notice = await routing.route_recall([
            {"role": "user", "content": "很短也可能需要历史事实"},
            {"role": "tool", "content": "不要被这个工具回执影响判断"},
        ], "user_webui:1")
        assert policy.level == expected and current_recall_policy("user_webui:1") == policy
        assert bool(notice) == (expected != "deep")
        if notice:
            assert "recall" in notice and "不代表没有相关记忆" in notice
        await routing.route_recall([], "user_webui:1")
    judge.assert_awaited_once()
    assert judge.await_args.kwargs["native_only"]
    assert "工具回执" not in str(judge.await_args.args[0])


@pytest.mark.parametrize("failure", ["missing", "invalid", "timeout", "error"])
async def test_semantic_route_failure_keeps_deep_recall(monkeypatch, failure):
    from agent.judgment import JudgmentReport, JudgmentSource, NoulAnswer
    from agent.memory import recall_routing as routing

    async def judge(*args, **kwargs):
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "error":
            raise RuntimeError("unavailable")
        answers = {} if failure == "missing" else {"history": NoulAnswer(noul=float("nan")),
                                                   "relations": NoulAnswer(noul=.01)}
        return JudgmentReport(source=JudgmentSource.TYPESAFE, answers=answers)

    monkeypatch.setattr(routing, "load_config", lambda: SimpleNamespace(
        enabled=True, native_configured=True, timeout=.02, model="decision-model"))
    monkeypatch.setattr(routing, "get_judgment_engine", lambda: SimpleNamespace(judge=judge))
    async with recall_turn("user_webui:1"):
        policy, notice = await asyncio.wait_for(routing.route_recall([
            {"role": "user", "content": "他的新地址和之前有什么变化"}], "user_webui:1"), timeout=1)
    assert policy.level == "deep" and not notice


@pytest.mark.parametrize("level,tags", [("off", ()), ("fast", ()), ("deep", ()), ("tags", ("user:qq:1", "project:甲"))])
async def test_explicit_recall_strategy_cannot_be_overridden_by_judgment(monkeypatch, level, tags):
    from agent.memory import recall_routing as routing

    judge_config = AsyncMock(side_effect=AssertionError("显式策略不应经过判断"))
    monkeypatch.setattr(routing, "load_config", judge_config)
    explicit = RecallPolicy(level=level, tags=tags)
    async with recall_turn("user_webui:1", explicit):
        policy, notice = await routing.route_recall([{"role": "user", "content": "嗯呐 对的 没错呢"}], "user_webui:1")
    assert policy == explicit and not notice
    judge_config.assert_not_called()


@pytest.mark.asyncio
async def test_entry_default_yields_to_explicit_and_sdk_policy():
    channel = RecallPolicy(level="off", background=False)
    sdk = RecallPolicy(level="fast")
    message = RecallPolicy(level="tags", tags=("project:test",))
    async with recall_turn("user_minecraft:1", default_policy=channel):
        assert current_recall_policy("user_minecraft:1") == channel
    with recall_options(sdk):
        async with recall_turn("user_minecraft:1", default_policy=channel):
            assert current_recall_policy("user_minecraft:1") == sdk
        async with recall_turn("user_minecraft:1", message, default_policy=channel):
            assert current_recall_policy("user_minecraft:1") == message
    async with recall_turn("user_qq:1"):
        assert current_recall_policy("user_qq:1") == default_recall_policy()


@pytest.mark.asyncio
async def test_channel_default_is_scoped_and_can_be_changed_during_turn():
    ready = asyncio.Event()
    async def game():
        async with recall_turn("user_minecraft:1", default_policy=RecallPolicy(level="off", background=False)):
            ready.set()
            await asyncio.sleep(.02)
            assert current_recall_policy("user_minecraft:1").level == "off"
            set_turn_recall_policy(RecallPolicy(level="fast"), "user_minecraft:1")
            assert current_recall_policy("user_minecraft:1").level == "fast"
    async def chat():
        await ready.wait()
        async with recall_turn("user_qq:1"):
            assert current_recall_policy("user_qq:1") == default_recall_policy()
    await asyncio.gather(game(), chat())


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
