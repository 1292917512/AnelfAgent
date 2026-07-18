"""工具结果预算截断（参考 hermes-agent budget_config）。

根据当前模型的上下文窗口动态计算工具结果的大小预算：
- 单条结果预算：窗口字符数的 15%（clamp 到 [8K, 100K]）
- 整轮结果预算：窗口字符数的 30%（clamp 到 [16K, 200K]）

小窗口模型自动收紧预算，防止大结果撑爆上下文；
关键工具（pinned）的结果不截断，避免丢失核心交互信息。
"""
from __future__ import annotations

from dataclasses import dataclass

_CHARS_PER_TOKEN = 4
_PER_RESULT_WINDOW_FRACTION = 0.15
_PER_TURN_WINDOW_FRACTION = 0.30

_MIN_RESULT_CHARS = 8_000
_MAX_RESULT_CHARS = 100_000
_MIN_TURN_CHARS = 16_000
_MAX_TURN_CHARS = 200_000

# 静态兜底预算（无法获取模型窗口时使用，与历史行为一致）
_FALLBACK_RESULT_CHARS = 8_000
_FALLBACK_TURN_CHARS = 24_000

# 关键工具：结果永不截断（交互确认类结果通常很短且必须完整）
PINNED_TOOLS = frozenset({
    "send_message", "end_reply", "schedule_reply",
})


@dataclass(frozen=True)
class ResultBudget:
    """一次思维会话的工具结果预算。"""

    per_result_chars: int
    per_turn_chars: int


def _clamp(value: float, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def budget_for_context_window(context_length: int) -> ResultBudget:
    """按模型上下文窗口（tokens）计算结果预算。"""
    if context_length <= 0:
        return ResultBudget(_FALLBACK_RESULT_CHARS, _FALLBACK_TURN_CHARS)
    window_chars = context_length * _CHARS_PER_TOKEN
    return ResultBudget(
        per_result_chars=_clamp(
            window_chars * _PER_RESULT_WINDOW_FRACTION,
            _MIN_RESULT_CHARS, _MAX_RESULT_CHARS,
        ),
        per_turn_chars=_clamp(
            window_chars * _PER_TURN_WINDOW_FRACTION,
            _MIN_TURN_CHARS, _MAX_TURN_CHARS,
        ),
    )


def resolve_result_limit(tool_name: str, budget: ResultBudget) -> int:
    """解析单个工具的结果字符上限（pinned 工具返回 0 表示不截断）。"""
    if tool_name in PINNED_TOOLS:
        return 0
    return budget.per_result_chars
