"""对话补充回忆的时序、归属、候选筛选与取消边界。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.judgment.gate import WorkDecision
from agent.memory.probe import DeepProbeHub
from agent.memory.probe_render import RecallDelta
from agent.memory.recall_policy import RecallPolicy
from agent.mind import recollection_refresh as refresh

SCOPE = "user_webui:42#main"


def dialogue(text):
    return [{"role": "user", "content": text, "_source": {"origin": "conversation", "scope": SCOPE}}]


@pytest.fixture
def worker(monkeypatch):
    hub = DeepProbeHub()
    hub.begin_reply(SCOPE, managed=True)
    monkeypatch.setattr(refresh, "deep_probe_hub", hub)
    monkeypatch.setattr(refresh, "current_recall_policy", lambda scope: RecallPolicy())
    monkeypatch.setattr(refresh, "get_config_bool", lambda key, default: default)
    monkeypatch.setattr(refresh, "get_config_int", lambda key, default: default)
    mind = SimpleNamespace(retriever=object())
    worker = refresh.DialogueRecollection(mind, SCOPE, dialogue("讨论办公室网络配置"))
    return worker


async def test_new_dialogue_is_nonblocking_and_ignores_execution_evidence(worker, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    queries = []

    async def candidates(query):
        queries.append(query)
        entered.set()
        await release.wait()
        return [RecallDelta("记忆", "网络事实", "mem:1", "网络事实")]

    monkeypatch.setattr(worker, "_candidates", candidates)
    judge = AsyncMock(return_value=WorkDecision(True, "未配置判断服务"))
    monkeypatch.setattr(refresh, "assess_optional_work", judge)
    messages = [
        {"role": "system", "content": "PRIVATE_PREFIX"},
        *dialogue("请检查办公室路由器原先的网络设置"),
        {"role": "assistant", "content": "INTERNAL_MONOLOGUE"},
        {"role": "tool", "content": "TOOL_LOG"},
        {"role": "user", "content": "FOREIGN_DIALOGUE", "_source": {"origin": "cross_channel"}},
    ]
    worker.offer(messages, watermark=(1, 1))
    assert worker.task is not None and not entered.is_set()
    await asyncio.wait_for(entered.wait(), 1)
    worker.offer(messages, watermark=(1, 1))
    assert len(queries) == 1 and not worker.state.rendered
    release.set()
    await worker.task
    sent = str(judge.await_args)
    assert all(word not in sent and word not in queries[0] for word in ("PRIVATE_PREFIX", "INTERNAL_MONOLOGUE", "TOOL_LOG", "FOREIGN_DIALOGUE"))
    assert SCOPE in sent and "路由器" in sent
    assert "网络事实" in worker.state.rendered
    assert not worker.state.ledger.seen_result("mem:1")
    refresh.deep_probe_hub.acknowledge(SCOPE, worker.state.rendered)
    assert worker.state.ledger.seen_result("mem:1")
    worker.offer(messages, watermark=(2, 2))
    assert worker.count == 1


async def test_newer_dialogue_supersedes_inflight_and_coalesces_pending(worker, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    queries = []

    async def candidates(query):
        queries.append(query)
        if len(queries) == 1:
            entered.set()
            await release.wait()
        return [RecallDelta("记忆", query, f"mem:{len(queries)}", query)]

    monkeypatch.setattr(worker, "_candidates", candidates)
    judge = AsyncMock(return_value=WorkDecision(True, "selected", selected=(0,)))
    monkeypatch.setattr(refresh, "assess_optional_work", judge)
    worker.offer(dialogue("请检查第一个项目的网络历史"), watermark=(1, 1))
    await asyncio.wait_for(entered.wait(), 1)
    worker.offer(dialogue("改为检查第二个项目的部署历史"), watermark=(2, 2))
    worker.offer(dialogue("最终只检查第三个项目的备份历史"), watermark=(3, 3))
    release.set()
    await worker.task
    assert len(queries) == 2 and "第三个" in queries[1]
    assert "第一个" not in worker.state.rendered and "第二个" not in worker.state.rendered
    assert "第三个" in worker.state.rendered
    judge.assert_awaited_once()


async def test_candidate_selection_and_manual_read_race(worker, monkeypatch):
    deltas = [RecallDelta("技能", "技能候选", "skill:deploy", "技能候选"),
              RecallDelta("记忆", "不相关事实", "mem:2", "不相关事实"),
              RecallDelta("记忆", "相关事实", "mem:3", "相关事实")]
    monkeypatch.setattr(worker, "_candidates", AsyncMock(return_value=deltas))

    async def judge(*args, **kwargs):
        refresh.deep_probe_hub.record(SCOPE, identifiers=["skill:deploy"])
        return WorkDecision(True, "selected", selected=(0, 2))

    monkeypatch.setattr(refresh, "assess_optional_work", judge)
    worker.offer(dialogue("请检查部署文档相关的备份策略"), watermark=(1, 1))
    await worker.task
    assert "相关事实" in worker.state.rendered
    assert "不相关事实" not in worker.state.rendered and "技能候选" not in worker.state.rendered


@pytest.mark.parametrize("end", ["close", "replace", "stop"])
async def test_reply_lifecycle_rejects_late_results(worker, monkeypatch, end):
    entered = asyncio.Event()

    async def candidates(query):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return [RecallDelta("记忆", "旧结果", "mem:1", "旧结果")]

    monkeypatch.setattr(worker, "_candidates", candidates)
    judge = AsyncMock(return_value=WorkDecision(True, "local"))
    monkeypatch.setattr(refresh, "assess_optional_work", judge)
    worker.offer(dialogue("请回忆办公室相关的网络配置"), watermark=(1, 1))
    await asyncio.wait_for(entered.wait(), 1)
    if end == "close":
        await worker.close()
    elif end == "replace":
        refresh.deep_probe_hub.begin_reply(SCOPE, managed=True)
    else:
        refresh.deep_probe_hub.stop(SCOPE)
    await worker.task
    assert not refresh.deep_probe_hub.current_render(SCOPE)
    judge.assert_not_awaited()


@pytest.mark.parametrize("policy", [RecallPolicy(level="off"), RecallPolicy(level="tags"), RecallPolicy(background=False), RecallPolicy(level="fast", tags=("topic:network",))])
async def test_disabled_and_scoped_policies_never_start_background(worker, monkeypatch, policy):
    monkeypatch.setattr(refresh, "current_recall_policy", lambda scope: policy)
    worker.offer(dialogue("请查一下办公室历史网络设置"), watermark=(1, 1))
    assert worker.task is None


async def test_budget_cancels_slow_branch_without_calling_judge(worker, monkeypatch):
    cancelled = asyncio.Event()

    async def candidates(query):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(worker, "_candidates", candidates)
    monkeypatch.setattr(refresh, "get_config_float", lambda key, default: 0.1)
    judge = AsyncMock()
    monkeypatch.setattr(refresh, "assess_optional_work", judge)
    worker.offer(dialogue("请回忆办公室历史的网络设置"), watermark=(1, 1))
    await asyncio.wait_for(worker.task, 1)
    assert cancelled.is_set() and not worker.state.rendered
    judge.assert_not_awaited()


async def test_new_sender_is_not_deduplicated_as_previous_sender(worker, monkeypatch):
    lookup = AsyncMock(return_value=[])
    monkeypatch.setattr(worker, "_candidates", lookup)
    for index in (1, 2):
        worker.offer(dialogue(f"[uid:{index}] 请回忆我的办公室网络配置"), watermark=(index, index))
        await worker.task
    assert lookup.await_count == 2
    worker.offer(dialogue("[uid:2] 请回忆我的办公室网络配置"), watermark=(3, 3))
    assert lookup.await_count == 2


async def test_local_branches_parallelize_and_filter_private_ownership(worker, monkeypatch):
    from agent.memory.memory_types import MemorySearchResult

    memory_started, skills_started = asyncio.Event(), asyncio.Event()

    async def search(*args):
        memory_started.set()
        await skills_started.wait()
        return [MemorySearchResult(id="mem:1", snippet="public", score=1),
                MemorySearchResult(id="mem:2", snippet="private", score=1, sensitivity="private", tags=["user:qq:999"])]

    async def match(*args, **kwargs):
        skills_started.set()
        await memory_started.wait()
        return [(SimpleNamespace(name="deploy", description="Deploy safely", state=SimpleNamespace(value="active")), 0.9)]

    async def format_results(results, *args):
        return [RecallDelta("记忆", result.snippet, result.id, result.snippet) for result in results]

    worker.mind.retriever = SimpleNamespace(_apply_scope_boost=lambda results, scopes: results, _store=object())
    worker.mind._skills_enabled = lambda: True
    worker.mind.skill_matcher = SimpleNamespace(match=match)
    monkeypatch.setattr(refresh, "search_local", search)
    monkeypatch.setattr(refresh.deep_probe_hub, "_memory_deltas", format_results)
    deltas = await asyncio.wait_for(worker._candidates("network"), 1)
    assert [delta.result_id for delta in deltas] == ["mem:1", "skill:deploy"]


async def test_real_think_loop_keeps_message_order_and_adds_recall_only_to_next_call(worker, monkeypatch, anything):
    from helpers.think_loop_fakes import FakeMind, run_think_loop, tool_result

    from agent.memory import probe
    from agent.mind.tools import round_helpers
    from core.context_provider import ContextProviderRegistry, ProviderMeta
    from core.conversation_scope import bind_scope, reset_scope

    monkeypatch.setattr(probe, "deep_probe_hub", refresh.deep_probe_hub)
    ContextProviderRegistry.register(ProviderMeta(name="memory_deep_probe", provide_fn=probe._provide_probe))
    entered, release = asyncio.Event(), asyncio.Event()

    async def candidates(self, query):
        entered.set()
        await release.wait()
        return [RecallDelta("记忆", "备份保留七天", "mem:7", "备份保留七天")]

    monkeypatch.setattr(refresh.DialogueRecollection, "_candidates", candidates)
    monkeypatch.setattr(refresh, "assess_optional_work", AsyncMock(return_value=WorkDecision(True, "local")))
    mind = FakeMind(rounds=[tool_result("", ["recall"]), tool_result("", ["get_skill"]), tool_result("", ["end_reply"])], default_text=None)
    mind.retriever = object()
    anything.entity_scope = SCOPE
    prefix = [{"role": "system", "content": "STABLE_PREFIX", "_layer": "stable"}, *dialogue("讨论办公室网络配置")]
    original_invoke = mind._invoke_llm_unified

    async def fetch(mind, anything, since_ts, since_id=0):
        return [{"id": 50, "ts_ns": 9_000_000_000_000_000_000, "role": "user", "content": "也请检查备份保留的时间"}] if mind.llm_calls >= 1 and since_id < 50 else []

    async def invoke(*args, **kwargs):
        result = await original_invoke(*args, **kwargs)
        if mind.llm_calls == 2:
            await asyncio.wait_for(entered.wait(), 1)
            release.set()
            await asyncio.wait_for(refresh.deep_probe_hub.state_for(SCOPE).task, 1)
        return result

    monkeypatch.setattr(round_helpers, "_fetch_new_messages", fetch)
    monkeypatch.setattr(mind, "_invoke_llm_unified", invoke)
    token = bind_scope(SCOPE)
    try:
        await run_think_loop(mind, anything=anything, base_messages=prefix)
    finally:
        reset_scope(token)
    assert mind.llm_calls == 3
    assert all(messages[:len(prefix)] == prefix for messages in mind.sent_messages)
    assert "备份保留七天" not in str(mind.sent_messages[1])
    third = mind.sent_messages[2]
    assert [message["content"] for message in third if message["role"] == "user"] == ["讨论办公室网络配置", "也请检查备份保留的时间"]
    position = next(i for i, message in enumerate(third) if message.get("_source", {}).get("provider") == "memory_deep_probe")
    assert position > max(i for i, message in enumerate(third) if message["role"] == "tool")
    assert "备份保留七天" in third[position]["content"]
    assert refresh.deep_probe_hub.state_for(SCOPE).ledger.seen_result("mem:7")
