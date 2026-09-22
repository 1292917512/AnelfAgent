"""dedup 判断平台迁移路径测试：判断段映射 / 置信度门 / 合成段 / 失败语义。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import pytest

from agent.judgment.types import (
    ChoiceAnswer,
    JudgmentError,
    JudgmentReport,
    JudgmentSource,
    NoulAnswer,
)
from agent.memory import dedup
from agent.memory.memory_types import MemoryEntry, MemoryType
from core.tool_errors import ErrorCause


def _entry(entry_id: int, content: str) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        memory_type=MemoryType.SEMANTIC,
        content=content,
        timestamp=time.time(),
    )


class _FakeEngine:
    """引擎替身：返回预置答案，记录收到的 state/questions。"""

    def __init__(self, answers: Dict[str, Any]) -> None:
        self._answers = answers
        self.seen_questions: Dict[str, Any] = {}
        self.seen_state: Any = None

    async def judge(self, state: Any, questions: Dict[str, Any]) -> JudgmentReport:
        self.seen_state = state
        self.seen_questions = questions
        return JudgmentReport(
            answers=self._answers, source=JudgmentSource.TYPESAFE, model="jev-test"
        )


def _relation(choice: str, confidence: float = 1.0) -> ChoiceAnswer:
    options = list(dedup._RELATION_CRITERIA)
    return ChoiceAnswer(
        choice=choice,
        probabilities={opt: (1.0 if opt == choice else 0.0) for opt in options},
        confidence=confidence,
    )


def _target(choice: str, confidence: float = 1.0) -> ChoiceAnswer:
    return ChoiceAnswer(choice=choice, probabilities={choice: 1.0}, confidence=confidence)


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    answers: Dict[str, Any],
    *,
    synthesized: str = "合并后的记忆文本",
) -> _FakeEngine:
    engine = _FakeEngine(answers)
    monkeypatch.setattr(dedup, "get_judgment_engine", lambda: engine)
    monkeypatch.setattr(dedup, "light_llm", _FakeLight(synthesized))
    return engine


class _FakeLight:
    """light_llm 替身：返回预置合成文本；None 模拟合成失败。"""

    def __init__(self, text: Optional[str]) -> None:
        self._text = text
        self.prompts: List[str] = []

    async def __call__(self, prompt: str, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        if self._text is None:
            raise RuntimeError("合成失败")
        return self._text


_CANDIDATES = [_entry(1, "住在杭州西湖区"), _entry(2, "喜欢下雨天")]


class TestQuestionShape:
    async def test_questions_cover_all_judgments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        engine = _patch(monkeypatch, {"relation": _relation("novel")})

        await dedup.judge_write("新事实", _CANDIDATES)

        assert set(engine.seen_questions) == {"relation", "target", "merge_fit_1", "merge_fit_2"}
        # 结构化 state：新记忆 + 候选快照（序号标识，不暴露真实 id）
        assert engine.seen_state["new_memory"] == "新事实"
        assert [c["seq"] for c in engine.seen_state["candidates"]] == ["1", "2"]
        # target 选项含候选序号与 none 兜底
        target_q = engine.seen_questions["target"]
        assert set(target_q.criteria) == {"1", "2", "none"}


class TestSeqMapping:
    async def test_real_ids_hidden_and_mapped_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        big_candidates = [_entry(1001, "住在杭州西湖区"), _entry(2042, "喜欢下雨天")]
        engine = _patch(monkeypatch, {
            "relation": _relation("evolution"),
            "target": _target("2"),
        })

        decision = await dedup.judge_write("搬到了杭州滨江区", big_candidates)

        # 题面与 state 只出现 1..N 序号，真实长 id 不暴露给判断模型
        assert set(engine.seen_questions) == {"relation", "target", "merge_fit_1", "merge_fit_2"}
        assert set(engine.seen_questions["target"].criteria) == {"1", "2", "none"}
        for question in engine.seen_questions.values():
            assert "1001" not in str(question.instructions)
            assert "2042" not in str(question.instructions)
        for preview in engine.seen_state["candidates"]:
            assert "id" not in preview
        # 序号裁决映射回真实 id
        assert decision["action"] == "update"
        assert decision["target_id"] == 2042

    async def test_merge_seq_mapped_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        big_candidates = [_entry(1001, "住在杭州西湖区"), _entry(2042, "喜欢下雨天")]
        _patch(monkeypatch, {
            "relation": _relation("fragments"),
            "merge_fit_1": NoulAnswer(noul=0.2),
            "merge_fit_2": NoulAnswer(noul=0.9),
        })

        decision = await dedup.judge_write("在西湖区听雨", big_candidates)

        assert decision["action"] == "merge"
        assert decision["target_ids"] == [2042]


class TestActionMapping:
    async def test_novel_to_store_without_synthesis(self, monkeypatch: pytest.MonkeyPatch) -> None:
        light = _FakeLight("不应被调用")
        engine = _FakeEngine({"relation": _relation("novel")})
        monkeypatch.setattr(dedup, "get_judgment_engine", lambda: engine)
        monkeypatch.setattr(dedup, "light_llm", light)

        decision = await dedup.judge_write("全新事实", _CANDIDATES)

        assert decision == {"action": "store"}
        assert light.prompts == []  # store 热路径零 LLM 调用

    async def test_covered_to_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, {"relation": _relation("covered")})

        decision = await dedup.judge_write("喜欢下雨天", _CANDIDATES)
        assert decision == {"action": "skip"}

    async def test_evolution_to_update(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, {
            "relation": _relation("evolution"),
            "target": _target("1"),
        })

        decision = await dedup.judge_write("搬到了杭州滨江区", _CANDIDATES)

        assert decision["action"] == "update"
        assert decision["target_id"] == 1
        assert decision["content"] == "合并后的记忆文本"

    async def test_fragments_to_merge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, {
            "relation": _relation("fragments"),
            "merge_fit_1": NoulAnswer(noul=0.9),
            "merge_fit_2": NoulAnswer(noul=0.2),
        })

        decision = await dedup.judge_write("在杭州西湖区喜欢听雨", _CANDIDATES)

        assert decision["action"] == "merge"
        assert decision["target_ids"] == [1]  # 只有越过 0.5 的候选进合并集
        assert decision["content"] == "合并后的记忆文本"


class TestConservativeFallbacks:
    async def test_low_relation_confidence_stores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, {"relation": _relation("covered", confidence=0.1)})

        decision = await dedup.judge_write("喜欢下雨天", _CANDIDATES)
        assert decision == {"action": "store"}

    async def test_update_target_none_stores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, {
            "relation": _relation("evolution"),
            "target": _target("none"),
        })

        decision = await dedup.judge_write("搬到了杭州滨江区", _CANDIDATES)
        assert decision == {"action": "store"}

    async def test_update_target_low_confidence_stores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, {
            "relation": _relation("evolution"),
            "target": _target("1", confidence=0.05),
        })

        decision = await dedup.judge_write("搬到了杭州滨江区", _CANDIDATES)
        assert decision == {"action": "store"}

    async def test_merge_without_fit_stores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, {
            "relation": _relation("fragments"),
            "merge_fit_1": NoulAnswer(noul=0.3),
            "merge_fit_2": NoulAnswer(noul=0.1),
        })

        decision = await dedup.judge_write("在杭州西湖区喜欢听雨", _CANDIDATES)
        assert decision == {"action": "store"}

    async def test_synthesis_failure_stores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch(monkeypatch, {
            "relation": _relation("evolution"),
            "target": _target("1"),
        }, synthesized=None)

        decision = await dedup.judge_write("搬到了杭州滨江区", _CANDIDATES)
        assert decision == {"action": "store"}

    async def test_engine_failure_stores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Down:
            async def judge(self, state: Any, questions: Dict[str, Any]) -> JudgmentReport:
                raise JudgmentError("通道不可用", cause=ErrorCause.NETWORK, retryable=True)

        monkeypatch.setattr(dedup, "get_judgment_engine", lambda: _Down())

        decision = await dedup.judge_write("任何内容", _CANDIDATES)
        assert decision == {"action": "store"}

    async def test_no_candidates_stores(self) -> None:
        decision = await dedup.judge_write("任何内容", [])
        assert decision == {"action": "store"}
