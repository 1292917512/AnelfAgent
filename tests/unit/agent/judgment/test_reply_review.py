"""正文兜底仅在原生判断明确、一致地允许时投递。"""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from agent.judgment import engine as engine_module
from agent.judgment.engine import JudgmentConfig, JudgmentEngine
from agent.judgment.evidence import evidence_json
from agent.judgment.types import ChoiceAnswer, JudgmentError, JudgmentReport, JudgmentSource, NoulAnswer
from agent.mind.execution import reply_delivery as delivery
from core.config import ConfigManager
from core.tool_errors import ErrorCause


def report(
    probability: float = 1, intent: str = "reply", source: JudgmentSource = JudgmentSource.TYPESAFE,
    *, intent_probability: float = 1,
) -> JudgmentReport:
    probabilities = {key: 0.0 for key in ("reply", "internal", "duplicate", "wrong_target", "uncertain")}
    probabilities[intent] = intent_probability
    probabilities["uncertain" if intent != "uncertain" else "reply"] = 1 - intent_probability
    return JudgmentReport(source=source, model="test", answers={
        "appropriate": NoulAnswer(noul=probability),
        "intent": ChoiceAnswer(choice=intent, confidence=1, probabilities=probabilities),
    })


@pytest.fixture
def engine(monkeypatch):
    config = JudgmentConfig(True, "", "http://localhost:9000", "local", 30, True, "", "low", "local", "provider")
    monkeypatch.setattr(delivery, "load_config", lambda: config)
    monkeypatch.setattr(engine_module, "load_config", lambda model_id="": config)
    instance = JudgmentEngine()
    monkeypatch.setattr(delivery, "get_judgment_engine", lambda: instance)
    monkeypatch.setattr(instance, "_judge_fallback", AsyncMock(side_effect=AssertionError("must not fall back")))
    return instance


@pytest.mark.parametrize("probability,intent,intent_probability,approved", [
    (1, "reply", 1, True), (0.8, "reply", 0.8, True), (0.79, "reply", 1, False), (1, "reply", 0.79, False),
    (0, "internal", 1, False), (0.2, "duplicate", 1, False), (0, "wrong_target", 1, False),
    (1, "uncertain", 1, False), (1, "internal", 1, False), (1, "duplicate", 1, False),
    (1, "wrong_target", 1, False), (2, "reply", 1, False), (float("nan"), "reply", 1, False),
])
async def test_only_consistent_confident_approval_delivers(engine, monkeypatch, probability, intent, intent_probability, approved):
    native = AsyncMock(return_value=report(probability, intent, intent_probability=intent_probability))
    monkeypatch.setattr(engine, "_judge_native", native)
    result = await delivery.assess_reply("答复", "user_webui:1#a", [{"role": "user", "content": "问题"}])
    assert result.approved is approved
    assert native.await_count == 1
    assert 4.5 < native.await_args.args[0].timeout <= 5
    assert native.await_args.args[1]["target_scope"] == "user_webui:1#a"
    engine._judge_fallback.assert_not_awaited()


@pytest.mark.parametrize("change", ["missing_appropriate", "missing_intent", "bad_distribution", "fallback"])
async def test_invalid_or_non_native_answer_cannot_approve_delivery(engine, monkeypatch, change):
    answer = report()
    if change == "missing_appropriate":
        answer.answers.pop("appropriate")
    elif change == "missing_intent":
        answer.answers.pop("intent")
    elif change == "bad_distribution":
        answer.answers["intent"].probabilities["internal"] = 1
    else:
        answer.source = JudgmentSource.LLM_FALLBACK
    monkeypatch.setattr(engine, "_judge_native", AsyncMock(return_value=answer))
    assert not (await delivery.assess_reply("答复", "user_qq:1", [])).approved


@pytest.mark.parametrize("change", [{"base_url": ""}, {"enabled": False}])
async def test_unconfigured_or_disabled_does_not_deliver_or_call_any_model(engine, monkeypatch, change):
    config = replace(delivery.load_config(), **change)
    monkeypatch.setattr(delivery, "load_config", lambda: config)
    judge = AsyncMock()
    monkeypatch.setattr(engine, "judge", judge)
    assert not (await delivery.assess_reply("答案", "user_qq:1", [])).approved
    judge.assert_not_awaited()


async def test_first_network_failure_then_immediate_skip(engine, monkeypatch):
    native = AsyncMock(side_effect=JudgmentError("offline", cause=ErrorCause.NETWORK, retryable=True))
    monkeypatch.setattr(engine, "_judge_native", native)
    for _ in range(2):
        assert not (await delivery.assess_reply("答案", "user_qq:1", [])).approved
    native.assert_awaited_once()
    engine._judge_fallback.assert_not_awaited()


async def test_oversized_reply_skips_model_without_delivery(engine, monkeypatch):
    native = AsyncMock()
    monkeypatch.setattr(engine, "_judge_native", native)
    result = await delivery.assess_reply("字" * 12001, "user_qq:1", [])
    assert not result.approved and "超过审核预算" in result.reason
    native.assert_not_awaited()


async def test_review_uses_one_bounded_evidence_window_and_preserves_entire_candidate(engine, monkeypatch):
    native = AsyncMock(return_value=report())
    monkeypatch.setattr(engine, "_judge_native", native)
    candidate = "本次答复：" + "完整正文" * 800
    messages = [{"role": "user", "content": "[uid:42][channel:qq] 现在进展如何？"}]
    messages += [{"role": "assistant", "content": f"旧答复 {n}: " + "旧事实" * 1500,
                  "_source": {"origin": "conversation"}} for n in range(3)]
    messages += [{"role": "tool", "content": "执行结果" * 2000}, {"role": "assistant", "content": candidate}]
    result = await delivery.assess_reply(candidate, "user_qq:42", messages)
    assert result.approved
    sent = native.await_args.args[1]
    assert sent["candidate"] == candidate
    assert len(evidence_json(sent["conversation"])) <= 3000
    assert result.input_chars == len(evidence_json(sent)) <= 8000
    assert "delivered_replies" not in sent
    assert evidence_json(sent).count("本次答复") == 1
    assert "[uid:42][channel:qq]" in evidence_json(sent)


async def test_total_budget_includes_escaped_candidate_and_skips_inference(engine, monkeypatch):
    ConfigManager.set("judgment_auto_max_chars", 2000)
    native = AsyncMock()
    monkeypatch.setattr(engine, "_judge_native", native)
    result = await delivery.assess_reply('"\\' * 600, "user_qq:42", [])
    assert not result.approved and "超过审核预算" in result.reason
    native.assert_not_awaited()


async def test_custom_delivery_threshold_applies_to_both_answers(engine, monkeypatch):
    ConfigManager.set("judgment_delivery_confidence", 0.9)
    answer = report(0.95, intent_probability=0.89)
    monkeypatch.setattr(engine, "_judge_native", AsyncMock(return_value=answer))
    result = await delivery.assess_reply("这是给你的回复", "user_qq:1", [])
    assert not result.approved and "放行阈值 90%" in result.reason
    answer.answers["intent"].probabilities.update(reply=0.9, uncertain=0.1)
    assert (await delivery.assess_reply("这是给你的回复", "user_qq:1", [])).approved


async def test_invalid_configuration_cannot_approve_delivery(engine, monkeypatch):
    def unavailable():
        raise JudgmentError("config unavailable", cause=ErrorCause.CONFIG, retryable=False)
    monkeypatch.setattr(delivery, "load_config", unavailable)
    assert not (await delivery.assess_reply("答案", "user_qq:1", [])).approved
