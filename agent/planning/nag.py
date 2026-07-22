"""目标 nag 提醒 — 对齐 Claude Code todo_reminder 的启发式注入。

规划工具（goal CRUD）不常驻上下文；当某 scope 曾创建过目标、
且连续 N 轮未调用任何目标工具、距上次提醒也超过 N 轮时，
向执行上下文注入一次轻提醒（避免目标被遗忘或堆积）。
"""

from __future__ import annotations

from typing import Dict

# 对齐 Claude Code TODO_REMINDER_CONFIG：10 轮未使用 + 10 轮未提醒
ROUNDS_SINCE_USE = 10
ROUNDS_BETWEEN_NAGS = 10

GOAL_TOOL_NAMES = frozenset({
    "create_goal", "update_goal", "delete_goal", "list_goals", "get_goal",
})


class _ScopeState:
    __slots__ = ("round", "last_use_round", "last_nag_round", "ever_used")

    def __init__(self) -> None:
        self.round = 0
        self.last_use_round = 0
        self.last_nag_round = 0
        self.ever_used = False


_states: Dict[str, _ScopeState] = {}


def _state(scope: str) -> _ScopeState:
    return _states.setdefault(scope or "_global", _ScopeState())


def note_tools_used(scope: str, tool_names: list) -> None:
    """每轮工具执行后记录目标工具使用（由 think_loop 调用）。"""
    st = _state(scope)
    if any(name in GOAL_TOOL_NAMES for name in tool_names):
        st.last_use_round = st.round
        st.ever_used = True


def maybe_nag(scope: str) -> str:
    """每轮构建执行上下文时调用：返回提醒文本（或空串）。

    仅当该 scope 曾使用过目标工具（说明有目标存在过）时才提醒，
    避免对无目标场景制造噪音。
    """
    st = _state(scope)
    st.round += 1
    if not st.ever_used:
        return ""
    if st.round - st.last_use_round < ROUNDS_SINCE_USE:
        return ""
    if st.round - st.last_nag_round < ROUNDS_BETWEEN_NAGS:
        return ""
    st.last_nag_round = st.round
    return (
        f"[目标提醒] 你已有 {st.round - st.last_use_round} 轮未更新目标。"
        "如目标仍在进行，请用 list_goals 查看进度并更新；"
        "已完成的目标请用 update_goal 标记完成或用 delete_goal 删除，避免堆积。"
        "（请勿向用户提及本提醒）"
    )


def reset(scope: str) -> None:
    """清空 scope 状态（测试用）。"""
    _states.pop(scope or "_global", None)
