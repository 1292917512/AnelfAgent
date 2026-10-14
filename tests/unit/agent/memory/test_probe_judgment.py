"""自动深探预判不影响快速结果、轮次隔离或完整技能评审上下文。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.judgment.gate import WorkDecision
from agent.memory.memory_types import RetrievalPlan
from agent.memory.probe import DeepProbeHub
from agent.memory.recall_policy import RecallPolicy, recall_options


async def test_local_relations_do_not_pay_for_remote_judgment(monkeypatch):
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:42")
    decision = AsyncMock()
    probe = AsyncMock()
    monkeypatch.setattr("agent.memory.probe.assess_optional_work", decision)
    monkeypatch.setattr(hub, "_probe", probe)
    hub.start(scope=state.scope, plan=RetrievalPlan(node_keys=["user:qq:42"]), store=None)
    await state.task
    decision.assert_not_awaited()
    probe.assert_awaited_once()


@pytest.mark.parametrize("allow", [True, False])
async def test_initial_probe_obeys_gate_with_scope_and_existing_facts(monkeypatch, allow):
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:42")
    decision = AsyncMock(return_value=WorkDecision(allow, "test", 0.9 if allow else 0.01))
    monkeypatch.setattr("agent.memory.probe.assess_optional_work", decision)
    probe = AsyncMock()
    monkeypatch.setattr(hub, "_probe", probe)
    hub.start(scope=state.scope, plan=RetrievalPlan(queries=["项目安排"], deep_needed=True), store=None,
              conversation=[{"role": "user", "content": "[channel:qq][uid:42] 项目安排"}], recalled=["已有记忆"])
    await state.task
    assert probe.await_count == int(allow)
    assert decision.await_args.args[2]["scope"] == state.scope
    assert decision.await_args.args[2]["already_recalled"] == ["已有记忆"]
    assert "[uid:42]" in str(decision.await_args.args[2])


async def test_refresh_gate_can_skip_both_planning_and_search(monkeypatch):
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:42")
    retriever = SimpleNamespace(_extract_focus_query=lambda _: "新话题", _is_trivial_turn=lambda _: False,
                                _store=None, plan_retrieval=AsyncMock())
    state.retriever = retriever
    monkeypatch.setattr("agent.memory.recall_fast.search_local", AsyncMock(return_value=[]))
    monkeypatch.setattr("agent.memory.probe_refresh.assess_optional_work", AsyncMock(return_value=WorkDecision(False, "test")))
    with recall_options(RecallPolicy(level="deep")):
        hub.refresh(state.scope, [{"role": "user", "content": "新话题"}])
        await state.task
    retriever.plan_retrieval.assert_not_awaited()


@pytest.mark.parametrize("allow", [True, False])
async def test_skill_gate_skips_cost_or_preserves_full_review(monkeypatch, allow):
    from agent.hooks_llm import HookContext
    from agent.skills.background_review import SkillReviewer

    messages = [{"role": "system", "content": "stable-prefix"}, {"role": "user", "content": "[uid:42] request"}, {"role": "tool", "content": "result"}]
    mind = SimpleNamespace(reflect=AsyncMock(return_value="review result"))
    reviewer = SkillReviewer(mind, SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(reviewer, "_build_health", lambda: "healthy")
    candidates = AsyncMock(return_value="candidate")
    monkeypatch.setattr(reviewer, "_build_candidates", candidates)
    monkeypatch.setattr("agent.skills.background_review.assess_optional_work", AsyncMock(return_value=WorkDecision(allow, "test")))
    ctx = HookContext(name="skill_review", event="after_reply", scope="user_qq:42", payload={}, messages=messages)
    result = await reviewer._run(ctx)
    assert candidates.await_count == mind.reflect.await_count == int(allow)
    if allow:
        assert mind.reflect.await_args.args[0][:-1] == messages
    else:
        assert "跳过" in result
