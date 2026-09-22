"""普通模型回退通道 — 用聊天模型完成与 TypeSafe 同构的结构化判断。

提示词携带与原生通道相同的问题定义（id 除外），要求严格 JSON 输出；
解析后由本地合成最终答案：概率归一化、置信度按 TypeSafe 同公式
(n·p−1)/(n−1) 计算、Score 位置按概率加权。单题解析失败不拖垮整批——
成功答案照出、失败 id 记入报告 missing。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from agent.judgment.types import (
    Answer,
    ChoiceAnswer,
    ChoiceQuestion,
    JsonValue,
    JudgmentError,
    JudgmentUsage,
    NoulAnswer,
    NoulQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
    compute_confidence,
    question_payload,
)
from core.log import log
from core.tool_errors import ErrorCause

_SYSTEM_PROMPT = """你是结构化判断引擎。针对给定 state（被评判的内容）与 questions（问题定义），逐题独立评判，只输出 JSON。

题型与答案格式：
- choice：从 criteria 的选项键中选最合适的一项。输出 {"choice": "选项键", "probabilities": {"选项键": 概率}}——每个选项都要给概率，总和为 1。
- score：criteria 是有序等级数组（序号从 0 起）。输出 {"probabilities": {"等级序号字符串": 概率}}——每一级都要给概率，总和为 1。
- noul：判断命题是否成立。输出 {"noul": 概率}——1 表示确定成立，0 表示确定不成立。

纪律：
- 逐题独立评判，各题答案互不参照
- 概率如实反映不确定性：拿不准就把概率摊开，不要硬凑峰值
- criteria 中选项/等级的描述（含结构化对象）是评判依据，仔细对照
- 只输出一个 JSON 对象：{"answers": {"<问题id>": {...答案...}}}，不要输出任何其他文字"""


async def judge_via_llm(
    *,
    state: JsonValue,
    questions: Dict[str, Question],
    model_id: str,
    effort: str,
    timeout: float,
) -> Tuple[Dict[str, Answer], List[str], str, JudgmentUsage]:
    """经聊天模型批量评判，返回（答案字典, 缺失 id 列表, 实际模型名, 用量）。"""
    from agent.llm import get_llm_manager

    manager = get_llm_manager()
    client = None
    if model_id:
        client = manager.get_enabled_client(model_id)
        if client is None:
            log(f"回退判断模型不可用，走默认链: {model_id}", "DEBUG", tag="判断")
    result = await manager.chat_with_fallback(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _render_user_message(state, questions)},
        ],
        options={"reasoning_effort": effort} if effort else None,
        client=client,
        max_retries=1,
        timeout=timeout,
        purpose="judgment",
    )
    answers, missing = _parse_answers(_extract_json(result.content or ""), questions)
    if not answers:
        raise JudgmentError(
            "回退通道输出无法解析为有效答案", cause=ErrorCause.INTERNAL, retryable=True
        )
    usage = JudgmentUsage()
    if result.usage is not None:
        usage = JudgmentUsage(
            input_tokens=result.usage.prompt_tokens,
            output_tokens=result.usage.completion_tokens,
        )
    return answers, missing, result.model or "", usage


def _render_user_message(state: JsonValue, questions: Dict[str, Question]) -> str:
    question_defs = {qid: question_payload(q) for qid, q in questions.items()}
    state_text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, indent=2)
    return (
        f"state:\n{state_text}\n\n"
        f"questions:\n{json.dumps(question_defs, ensure_ascii=False, indent=2)}"
    )


def _extract_json(text: str) -> Dict[str, Any]:
    """从容错边界提取首个完整 JSON 对象（容忍首尾杂质文本）。"""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise JudgmentError("回退通道输出不含 JSON", cause=ErrorCause.INTERNAL, retryable=True)
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError) as exc:
        raise JudgmentError(
            "回退通道输出 JSON 解析失败", cause=ErrorCause.INTERNAL, retryable=True
        ) from exc
    if not isinstance(data, dict):
        raise JudgmentError("回退通道输出不是 JSON 对象", cause=ErrorCause.INTERNAL, retryable=True)
    return data


def _parse_answers(
    data: Dict[str, Any], questions: Dict[str, Question]
) -> Tuple[Dict[str, Answer], List[str]]:
    raw_answers = data.get("answers")
    if not isinstance(raw_answers, dict):
        raise JudgmentError(
            "回退通道输出缺少 answers 对象", cause=ErrorCause.INTERNAL, retryable=True
        )
    answers: Dict[str, Answer] = {}
    missing: List[str] = []
    for qid, question in questions.items():
        raw = raw_answers.get(qid)
        if not isinstance(raw, dict):
            missing.append(qid)
            continue
        try:
            answers[qid] = _synthesize_answer(question, raw)
        except JudgmentError:
            missing.append(qid)
            log(f"回退答案解析失败（跳过该题）: {qid}", "DEBUG", tag="判断")
    return answers, missing


def _synthesize_answer(question: Question, raw: Dict[str, Any]) -> Answer:
    if isinstance(question, ChoiceQuestion):
        return _synthesize_choice(question, raw)
    if isinstance(question, ScoreQuestion):
        return _synthesize_score(question, raw)
    if isinstance(question, NoulQuestion):
        return _synthesize_noul(raw)
    raise JudgmentError(  # pragma: no cover - 判别联合已穷尽
        f"未知问题类型: {type(question)}", cause=ErrorCause.INTERNAL, retryable=False
    )


def _synthesize_choice(question: ChoiceQuestion, raw: Dict[str, Any]) -> ChoiceAnswer:
    options = list(question.criteria.keys())
    probabilities = _normalize_probabilities(raw.get("probabilities"), options)
    choice = options[0]
    for option in options:
        if probabilities[option] > probabilities[choice]:
            choice = option
    return ChoiceAnswer(
        choice=choice,
        probabilities=probabilities,
        confidence=compute_confidence(list(probabilities.values())),
    )


def _synthesize_score(question: ScoreQuestion, raw: Dict[str, Any]) -> ScoreAnswer:
    levels = [str(index) for index in range(len(question.criteria))]
    probabilities = _normalize_probabilities(raw.get("probabilities"), levels)
    score = sum(index * probabilities[str(index)] for index in range(len(levels)))
    return ScoreAnswer(
        score=round(score, 4),
        probabilities=probabilities,
        legend={str(i): level for i, level in enumerate(question.criteria)},
        confidence=compute_confidence(list(probabilities.values())),
    )


def _synthesize_noul(raw: Dict[str, Any]) -> NoulAnswer:
    value = raw.get("noul")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise JudgmentError("noul 答案缺少数值", cause=ErrorCause.INTERNAL, retryable=True)
    return NoulAnswer(noul=max(0.0, min(1.0, float(value))))


def _normalize_probabilities(raw: Any, keys: List[str]) -> Dict[str, float]:
    """归一化概率分布：只认定义内的键，缺失补 0，负值钳制；全零退化为均匀分布。"""
    raw_map = raw if isinstance(raw, dict) else {}
    values: Dict[str, float] = {}
    for key in keys:
        value = raw_map.get(key)
        values[key] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
        values[key] = max(0.0, values[key])
    total = sum(values.values())
    if total <= 0:
        uniform = 1.0 / len(keys)
        return {key: uniform for key in keys}
    return {key: round(values[key] / total, 4) for key in keys}
