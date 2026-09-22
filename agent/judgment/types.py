"""判断类型体系 — Choice/Score/Noul 三原语的问题、答案与模块错误。

类型契约对齐 TypeSafe System One API（POST /v1/systemone）：
问题以 id 为键批量提交、并行独立评判；答案按同键返回，携带概率分布
与置信度（Noul 的答案值本身即完整分布描述）。本模块同时是原生通道
与回退通道的统一产出格式——两条通道的答案模型完全一致。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
)
from typing_extensions import Annotated

from core.tool_errors import ErrorCause

# instructions / criteria 描述项接受的结构（对齐 TypeSafe EntryType）
EntryValue = Union[str, Dict[str, JsonValue], List[JsonValue], None]


class JudgmentError(Exception):
    """判断调用失败。cause/retryable 供引擎决策降级与工具层归因。"""

    def __init__(self, message: str, *, cause: ErrorCause, retryable: bool) -> None:
        super().__init__(message)
        self.cause = cause
        self.retryable = retryable


class JudgmentSource(str, Enum):
    """判断结果来源通道。"""

    TYPESAFE = "typesafe"
    LLM_FALLBACK = "llm_fallback"


# ---------------------------------------------------------------------------
# 问题
# ---------------------------------------------------------------------------

class NoulCriteria(BaseModel):
    """Noul 的是非边界定义（可选；边界模糊时用以钉死两侧语义）。"""

    true: EntryValue = None
    false: EntryValue = None


class _QuestionBase(BaseModel):
    """问题公共字段：instructions 为评判指令（字符串或结构化对象）。"""

    model_config = ConfigDict(extra="forbid")

    instructions: EntryValue

    @field_validator("instructions")
    @classmethod
    def _instructions_present(cls, value: EntryValue) -> EntryValue:
        if value is None or value == "":
            raise ValueError("instructions 不能为空")
        return value


class ChoiceQuestion(_QuestionBase):
    """Choice 问题：从固定选项集合中选一项。criteria 为 选项名 → 描述。"""

    type: Literal["choice"] = "choice"
    criteria: Dict[str, EntryValue]

    @field_validator("criteria")
    @classmethod
    def _options_bounded(cls, value: Dict[str, EntryValue]) -> Dict[str, EntryValue]:
        if not 1 <= len(value) <= 255:
            raise ValueError("choice 选项数需在 1~255 之间")
        if any(not key.strip() for key in value):
            raise ValueError("choice 选项名不能为空")
        return value


class ScoreQuestion(_QuestionBase):
    """Score 问题：在有序等级上评分。criteria 为低到高的等级描述数组。"""

    type: Literal["score"] = "score"
    criteria: List[EntryValue]

    @field_validator("criteria")
    @classmethod
    def _levels_bounded(cls, value: List[EntryValue]) -> List[EntryValue]:
        if not 2 <= len(value) <= 10:
            raise ValueError("score 等级数需在 2~10 之间")
        return value


class NoulQuestion(_QuestionBase):
    """Noul 问题：是非评判。criteria 可选，定义 true/false 两侧边界。"""

    type: Literal["noul"] = "noul"
    criteria: Optional[NoulCriteria] = None


Question = Annotated[
    Union[ChoiceQuestion, ScoreQuestion, NoulQuestion],
    Field(discriminator="type"),
]

_QUESTION_ADAPTER: TypeAdapter[Question] = TypeAdapter(Question)


def parse_questions(items: Any) -> Dict[str, Question]:
    """把工具入参（[{id, type, instructions, criteria}] 或 {id: {...}}）解析为问题字典。

    id 是调用方选择的回传键，不发给模型；重复/缺失 id 与无效定义
    一律归为参数错误。
    """
    if isinstance(items, dict):
        entries = [(str(key), value) for key, value in items.items()]
    elif isinstance(items, list):
        entries = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise JudgmentError(
                    f"第 {index + 1} 个问题不是对象", cause=ErrorCause.PARAM, retryable=False
                )
            qid = str(item.get("id") or "").strip()
            if not qid:
                raise JudgmentError(
                    f"第 {index + 1} 个问题缺少 id", cause=ErrorCause.PARAM, retryable=False
                )
            payload = {key: value for key, value in item.items() if key != "id"}
            entries.append((qid, payload))
    else:
        raise JudgmentError("questions 需为非空数组或对象", cause=ErrorCause.PARAM, retryable=False)
    if not entries:
        raise JudgmentError("questions 不能为空", cause=ErrorCause.PARAM, retryable=False)

    questions: Dict[str, Question] = {}
    for qid, payload in entries:
        if not qid.strip():
            raise JudgmentError("问题 id 不能为空", cause=ErrorCause.PARAM, retryable=False)
        if qid in questions:
            raise JudgmentError(f"问题 id 重复: '{qid}'", cause=ErrorCause.PARAM, retryable=False)
        try:
            questions[qid] = _QUESTION_ADAPTER.validate_python(payload)
        except Exception as exc:
            raise JudgmentError(
                f"问题 '{qid}' 定义无效: {_first_validation_message(exc)}",
                cause=ErrorCause.PARAM,
                retryable=False,
            ) from exc
    return questions


def _first_validation_message(exc: Exception) -> str:
    """提取 pydantic 校验错误的首条可读信息。"""
    errors = getattr(exc, "errors", None)
    if callable(errors):
        details = exc.errors()
        if details:
            first = details[0]
            loc = ".".join(str(part) for part in first.get("loc", ()))
            return f"{loc}: {first.get('msg', '')}".strip(": ")
    text = str(exc).strip()
    return text[:200] if text else type(exc).__name__


def question_payload(question: Question) -> Dict[str, Any]:
    """问题的 wire 序列化：仅剔除顶层 None 字段（criteria 内的 null 描述是合法语义，保留）。"""
    data = question.model_dump()
    return {key: value for key, value in data.items() if value is not None}


# ---------------------------------------------------------------------------
# 答案
# ---------------------------------------------------------------------------

class ChoiceAnswer(BaseModel):
    """Choice 答案：选中项 + 全选项概率分布 + 分布集中度置信度。"""

    type: Literal["choice"] = "choice"
    choice: str
    probabilities: Dict[str, float]
    confidence: float


class ScoreAnswer(BaseModel):
    """Score 答案：概率加权等级位置 + 各级概率 + 置信度 + 等级描述回查表。"""

    type: Literal["score"] = "score"
    score: float
    probabilities: Dict[str, float]
    legend: Dict[str, EntryValue]
    confidence: float


class NoulAnswer(BaseModel):
    """Noul 答案：命题成立的概率（0=否，1=是；值本身即完整分布，无 confidence）。"""

    type: Literal["noul"] = "noul"
    noul: float


Answer = Union[ChoiceAnswer, ScoreAnswer, NoulAnswer]


def compute_confidence(probabilities: List[float]) -> float:
    """由概率分布计算置信度（与 TypeSafe 同公式：(n·p−1)/(n−1)）。

    全部概率集中于一项时为 1.0，完全均匀分布时为 0.0；单一选项恒为 1.0。
    """
    n = len(probabilities)
    if n <= 1:
        return 1.0
    peak = max(probabilities)
    return max(0.0, min(1.0, (n * peak - 1.0) / (n - 1.0)))


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

class JudgmentUsage(BaseModel):
    """一次判断的 token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0


class JudgmentReport(BaseModel):
    """一次判断调用的完整结果（双通道统一格式）。"""

    answers: Dict[str, Answer]
    source: JudgmentSource
    model: str = ""
    usage: JudgmentUsage = Field(default_factory=JudgmentUsage)
    missing: List[str] = Field(default_factory=list)
