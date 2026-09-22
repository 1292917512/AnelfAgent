"""judgment 回退通道测试：LLM 输出的答案合成（归一化/置信度/部分缺失/容错）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from agent.judgment import bridge
from agent.judgment.types import (
    ChoiceAnswer,
    JudgmentError,
    NoulAnswer,
    ScoreAnswer,
    parse_questions,
)
from agent.llm.types import ChatResult


class _FakeManager:
    """chat_with_fallback 替身：返回预置文本。"""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: List[Dict[str, Any]] = []

    async def chat_with_fallback(
        self,
        messages: List[Dict[str, Any]],
        *,
        options: Optional[Dict[str, Any]] = None,
        client: Any = None,
        max_retries: int = 2,
        timeout: float = 120.0,
        purpose: str = "internal",
        stream: bool = False,
        record_usage: bool = True,
    ) -> ChatResult:
        self.calls.append({"messages": messages, "options": options, "purpose": purpose})
        return ChatResult(content=self._content, model="fake-model")

    def get_enabled_client(self, name: str) -> None:
        return None


def _questions() -> Dict[str, Any]:
    return parse_questions([
        {"id": "dept", "type": "choice", "instructions": "哪个团队",
         "criteria": {"returns": "退换", "shipping": "物流"}},
        {"id": "sev", "type": "score", "instructions": "严重程度", "criteria": ["轻", "中", "重"]},
        {"id": "urgent", "type": "noul", "instructions": "是否紧急"},
    ])


def _llm_payload() -> str:
    return json.dumps({
        "answers": {
            "dept": {"choice": "returns", "probabilities": {"returns": 0.9, "shipping": 0.1}},
            "sev": {"probabilities": {"0": 0.0, "1": 0.57, "2": 0.43}},
            "urgent": {"noul": 0.93},
        }
    })


async def test_bridge_synthesizes_all_types(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _FakeManager(_llm_payload())
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: manager)

    answers, missing, model, _usage = await bridge.judge_via_llm(
        state="文本", questions=_questions(), model_id="", effort="low", timeout=30.0,
    )

    assert missing == [] and model == "fake-model"
    choice = answers["dept"]
    assert isinstance(choice, ChoiceAnswer)
    assert choice.choice == "returns"
    assert choice.confidence == pytest.approx((2 * 0.9 - 1) / 1)
    score = answers["sev"]
    assert isinstance(score, ScoreAnswer)
    assert score.score == pytest.approx(0.57 + 2 * 0.43, abs=1e-3)
    assert score.legend == {"0": "轻", "1": "中", "2": "重"}
    noul = answers["urgent"]
    assert isinstance(noul, NoulAnswer) and noul.noul == pytest.approx(0.93)
    # 思考档与用途标记透传
    assert manager.calls[0]["options"] == {"reasoning_effort": "low"}
    assert manager.calls[0]["purpose"] == "judgment"


async def test_bridge_normalizes_and_fills(monkeypatch: pytest.MonkeyPatch) -> None:
    """概率不归一/缺选项/多未知键：归一化到定义键集合。"""
    payload = json.dumps({
        "answers": {
            "dept": {"probabilities": {"returns": 9, "unknown_opt": 5}},
            "sev": {"probabilities": {"2": 1}},
            "urgent": {"noul": 1.7},
        }
    })
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: _FakeManager(payload))

    answers, missing, _m, _u = await bridge.judge_via_llm(
        state="文本", questions=_questions(), model_id="", effort="", timeout=30.0,
    )

    assert missing == []
    dept = answers["dept"]
    assert isinstance(dept, ChoiceAnswer)
    assert set(dept.probabilities) == {"returns", "shipping"}  # 未知键剔除、缺失补 0
    assert dept.probabilities["returns"] == pytest.approx(1.0)
    assert dept.choice == "returns"
    sev = answers["sev"]
    assert isinstance(sev, ScoreAnswer) and sev.score == pytest.approx(2.0)
    urgent = answers["urgent"]
    assert isinstance(urgent, NoulAnswer) and urgent.noul == 1.0  # 钳制到 0..1


async def test_bridge_partial_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """单题解析失败不拖垮整批：成功照出、失败进 missing。"""
    payload = json.dumps({
        "answers": {
            "dept": {"probabilities": {"returns": 1.0}},
            "urgent": {"noul": "不太好说"},
        }
    })
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: _FakeManager(payload))

    answers, missing, _m, _u = await bridge.judge_via_llm(
        state="文本", questions=_questions(), model_id="", effort="", timeout=30.0,
    )

    assert set(answers) == {"dept"}
    assert sorted(missing) == ["sev", "urgent"]


async def test_bridge_garbage_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: _FakeManager("没有 JSON"))

    with pytest.raises(JudgmentError):
        await bridge.judge_via_llm(
            state="文本", questions=_questions(), model_id="", effort="", timeout=30.0,
        )


async def test_bridge_all_zero_probs_uniform(monkeypatch: pytest.MonkeyPatch) -> None:
    """全零概率退化为均匀分布（置信度 0，如实反映不确定）。"""
    payload = json.dumps({"answers": {"urgent": {"noul": 0.5}, "dept": {"probabilities": {}},
                                      "sev": {"probabilities": {}}}})
    monkeypatch.setattr("agent.llm.get_llm_manager", lambda: _FakeManager(payload))

    answers, missing, _m, _u = await bridge.judge_via_llm(
        state="文本", questions=_questions(), model_id="", effort="", timeout=30.0,
    )

    assert missing == []
    dept = answers["dept"]
    assert isinstance(dept, ChoiceAnswer)
    assert dept.confidence == pytest.approx(0.0)
    assert dept.probabilities == {"returns": 0.5, "shipping": 0.5}
