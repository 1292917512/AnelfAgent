"""回复审核只认可原生、完整且一致的答案；离线或故障不投递。"""

from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from agent.judgment import engine as engine_module
from agent.judgment.engine import JudgmentConfig, JudgmentEngine
from agent.judgment.types import ChoiceAnswer, JudgmentError, JudgmentReport, JudgmentSource, NoulAnswer
from agent.mind.tools import reply_delivery as delivery
from core.tool_errors import ErrorCause


def report(probability=1, intent="reply", source=JudgmentSource.TYPESAFE):
    return JudgmentReport(source=source, model="test", answers={
        "appropriate": NoulAnswer(noul=probability),
        "intent": ChoiceAnswer(choice=intent, confidence=1, probabilities={
            key: float(key == intent) for key in ["reply", "internal", "duplicate", "wrong_target", "uncertain"]}),
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


@pytest.mark.parametrize("probability,intent,approved", [(1, "reply", True), (0.8, "reply", True), (0.79, "reply", False),
    (1, "internal", False), (1, "duplicate", False), (1, "wrong_target", False), (1, "uncertain", False),
    (2, "reply", False), (float("nan"), "reply", False)])
async def test_both_answers_must_clearly_agree(engine, monkeypatch, probability, intent, approved):
    native = AsyncMock(return_value=report(probability, intent))
    monkeypatch.setattr(engine, "_judge_native", native)
    result = await delivery.assess_reply("答复", "user_webui:1#a", [{"role": "user", "content": "问题"}])
    assert result.approved is approved
    assert native.await_count == 1
    assert 4.5 < native.await_args.args[0].timeout <= 5
    assert native.await_args.args[1]["target_scope"] == "user_webui:1#a"
    engine._judge_fallback.assert_not_awaited()


@pytest.mark.parametrize("change", ["missing", "bad_distribution", "fallback"])
async def test_invalid_or_non_native_answer_rejects(engine, monkeypatch, change):
    answer = report()
    if change == "missing":
        answer.answers.pop("appropriate")
    elif change == "bad_distribution":
        answer.answers["intent"].probabilities["internal"] = 1
    else:
        answer.source = JudgmentSource.LLM_FALLBACK
    monkeypatch.setattr(engine, "_judge_native", AsyncMock(return_value=answer))
    assert not (await delivery.assess_reply("答复", "user_qq:1", [])).approved


async def test_unconfigured_does_not_call_any_model(engine, monkeypatch):
    config = replace(delivery.load_config(), base_url="")
    monkeypatch.setattr(delivery, "load_config", lambda: config)
    judge = AsyncMock()
    monkeypatch.setattr(engine, "judge", judge)
    assert (await delivery.assess_reply("答案", "user_qq:1", [])).approved
    judge.assert_not_awaited()


async def test_first_network_failure_then_immediate_skip(engine, monkeypatch):
    native = AsyncMock(side_effect=JudgmentError("offline", cause=ErrorCause.NETWORK, retryable=True))
    monkeypatch.setattr(engine, "_judge_native", native)
    for _ in range(2):
        assert not (await delivery.assess_reply("答案", "user_qq:1", [])).approved
    native.assert_awaited_once()
    engine._judge_fallback.assert_not_awaited()


async def test_oversized_reply_is_not_sent_without_review(engine, monkeypatch):
    native = AsyncMock()
    monkeypatch.setattr(engine, "_judge_native", native)
    result = await delivery.assess_reply("字" * 12001, "user_qq:1", [])
    assert not result.approved and "send_message" in result.reason
    native.assert_not_awaited()
