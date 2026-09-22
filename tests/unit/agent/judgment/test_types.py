"""judgment 类型体系测试：问题解析校验 / 置信度公式 / wire 序列化。"""

from __future__ import annotations

import pytest

from agent.judgment.types import (
    ChoiceQuestion,
    JudgmentError,
    NoulQuestion,
    ScoreQuestion,
    compute_confidence,
    parse_questions,
    question_payload,
)


class TestParseQuestions:
    def test_list_form(self) -> None:
        questions = parse_questions([
            {"id": "a", "type": "noul", "instructions": "成立吗？"},
            {"id": "b", "type": "choice", "instructions": "选哪个", "criteria": {"x": "X", "y": None}},
            {"id": "c", "type": "score", "instructions": "几分", "criteria": ["低", "高"]},
        ])
        assert list(questions) == ["a", "b", "c"]
        assert isinstance(questions["a"], NoulQuestion)
        assert isinstance(questions["b"], ChoiceQuestion)
        assert isinstance(questions["c"], ScoreQuestion)
        assert questions["a"].criteria is None

    def test_dict_form(self) -> None:
        questions = parse_questions({
            "a": {"type": "noul", "instructions": "成立吗？"},
        })
        assert isinstance(questions["a"], NoulQuestion)

    def test_noul_criteria_true_false(self) -> None:
        questions = parse_questions([{
            "id": "a", "type": "noul", "instructions": "成立吗？",
            "criteria": {"true": "明确成立", "false": "完全不成立"},
        }])
        criteria = questions["a"].criteria
        assert criteria is not None and criteria.true == "明确成立"

    def test_empty_rejected(self) -> None:
        with pytest.raises(JudgmentError):
            parse_questions([])

    def test_missing_id_rejected(self) -> None:
        with pytest.raises(JudgmentError):
            parse_questions([{"type": "noul", "instructions": "x"}])

    def test_duplicate_id_rejected(self) -> None:
        with pytest.raises(JudgmentError):
            parse_questions([
                {"id": "a", "type": "noul", "instructions": "x"},
                {"id": "a", "type": "noul", "instructions": "y"},
            ])

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(JudgmentError):
            parse_questions([{"id": "a", "type": "guess", "instructions": "x"}])

    def test_choice_options_bounds(self) -> None:
        with pytest.raises(JudgmentError):
            parse_questions([{"id": "a", "type": "choice", "instructions": "x", "criteria": {}}])

    def test_score_levels_bounds(self) -> None:
        with pytest.raises(JudgmentError):
            parse_questions([{"id": "a", "type": "score", "instructions": "x", "criteria": ["仅一级"]}])

    def test_structured_instructions(self) -> None:
        questions = parse_questions([{
            "id": "a", "type": "noul",
            "instructions": {"question": "是同一人吗？", "candidate": {"name": "张三"}},
        }])
        assert isinstance(questions["a"].instructions, dict)


class TestConfidence:
    def test_peak(self) -> None:
        assert compute_confidence([1.0, 0.0, 0.0]) == pytest.approx(1.0)

    def test_flat(self) -> None:
        assert compute_confidence([1 / 3] * 3) == pytest.approx(0.0)

    def test_partial(self) -> None:
        # (3×0.9−1)/2 = 0.85
        assert compute_confidence([0.9, 0.06, 0.04]) == pytest.approx(0.85)

    def test_single_option(self) -> None:
        assert compute_confidence([0.7]) == 1.0

    def test_two_options_split(self) -> None:
        # (2×0.5−1)/1 = 0
        assert compute_confidence([0.5, 0.5]) == pytest.approx(0.0)


class TestQuestionPayload:
    def test_top_level_none_dropped_but_criteria_null_kept(self) -> None:
        questions = parse_questions([
            {"id": "a", "type": "noul", "instructions": "x"},
            {"id": "b", "type": "choice", "instructions": "y", "criteria": {"m": None}},
        ])
        noul_payload = question_payload(questions["a"])
        assert "criteria" not in noul_payload  # 顶层 None 剔除
        choice_payload = question_payload(questions["b"])
        assert choice_payload["criteria"] == {"m": None}  # 选项的 null 描述保留
