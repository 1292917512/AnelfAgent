"""自动深探预判不影响快速结果、轮次隔离或完整技能评审上下文。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.judgment.gate import WorkDecision
from agent.memory.memory_types import RetrievalPlan
from agent.memory.probe import DeepProbeHub


async def test_local_relations_do_not_pay_for_remote_judgment(monkeypatch):
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:42")
    decision = AsyncMock()
    probe = AsyncMock()
    monkeypatch.setattr("agent.memory.probe.assess_optional_work", decision)
    monkeypatch.setattr(hub, "_probe", probe)
    await hub.collect(scope=state.scope, plan=RetrievalPlan(node_keys=["user:qq:42"]), store=None)
    await state.task
    decision.assert_not_awaited()
    probe.assert_awaited_once()


@pytest.mark.parametrize("allow", [True, False])
async def test_initial_probe_obeys_gate_using_only_dialogue_and_scope(monkeypatch, allow):
    hub = DeepProbeHub()
    state = hub.begin_reply("user_qq:42")
    decision = AsyncMock(return_value=WorkDecision(allow, "test", 0.9 if allow else 0.01))
    monkeypatch.setattr("agent.memory.probe.assess_optional_work", decision)
    probe = AsyncMock()
    monkeypatch.setattr(hub, "_probe", probe)
    await hub.collect(scope=state.scope, plan=RetrievalPlan(queries=["项目安排"], deep_needed=True), store=None,
              conversation=[{"role": "user", "content": "[channel:qq][uid:42] 项目安排"},
                            {"role": "tool", "content": "internal tool result"},
                            {"role": "assistant", "content": "internal plan", "_layer": "provider"}])
    await state.task
    assert probe.await_count == int(allow)
    assert decision.await_args.args[2]["scope"] == state.scope
    assert set(decision.await_args.args[2]) == {"scope", "conversation"}
    assert "[uid:42]" in str(decision.await_args.args[2])
    assert "internal" not in str(decision.await_args.args[2])


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
