"""执行日志 — 每轮回复完整操作摘要的环形缓冲与按需查询。

对话历史中只入库执行摘要的尾部（最近几条），长轮次不再以千字符级
摘要挤占上下文窗口；完整清单记录在本模块的 per-scope 环形缓冲
（进程内、固定深度），AI 需要回顾此前操作时经 ``get_execution_log``
按需取回。

通过 deferred_tool 模式注册（group="thinking"），bootstrap 阶段激活。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Dict, List

from entities._sdk import deferred_tool

#: 单会话保留的最近回复轮数
LOG_DEPTH = 8


@dataclass(slots=True)
class _LogEntry:
    """单轮回复的执行摘要记录。"""

    ts: float
    iterations: int
    summary: str


_logs: Dict[str, deque] = {}


def record(scope: str, summary: str, iterations: int = 0) -> None:
    """记录一轮回复的完整执行摘要（scope 为空的调用不记录）。"""
    if not scope or not summary:
        return
    _logs.setdefault(scope, deque(maxlen=LOG_DEPTH)).append(
        _LogEntry(ts=time.time(), iterations=iterations, summary=summary),
    )


def recent(scope: str, turns: int = 1) -> List[_LogEntry]:
    """取 scope 最近 turns 轮的执行摘要（新到旧）。"""
    entries = _logs.get(scope)
    if not entries:
        return []
    n = max(1, min(turns, len(entries)))
    return list(entries)[-n:][::-1]


def reset(scope: str = "") -> None:
    """清空缓冲（测试隔离用；带 scope 时只清该会话）。"""
    if scope:
        _logs.pop(scope, None)
    else:
        _logs.clear()


def _current_scope() -> str:
    from agent.mind.tool_activation import ToolActivationManager
    scope = ToolActivationManager.current_scope()
    return scope if scope and scope.startswith(("user_", "group_")) else ""


@deferred_tool(
    group="thinking", tags=["core"], source="mind.session", concurrency_safe=True,
    description="查看本会话最近几轮回复的完整已执行操作清单（每次工具调用的参数与"
                "结果预览，按轮分组）。对话历史里只保留摘要尾部，需要回顾此前完整"
                "操作（如核对做过什么、避免重复、补写总结）时使用。",
)
async def get_execution_log(turns: int = 1) -> str:
    """查看本会话最近几轮回复的完整执行日志。

    Args:
        turns: 回溯的回复轮数（默认 1 = 最近一轮，最多 8 轮）
    """
    scope = _current_scope()
    if not scope:
        return "当前无会话上下文，无法查询执行日志。"

    entries = recent(scope, turns)
    if not entries:
        return (
            f"本会话暂无执行日志记录（进程内保留最近 {LOG_DEPTH} 轮，"
            "重启后为空）。"
        )

    lines = [f"[执行日志] 本会话共 {len(_logs[scope])} 轮可查，返回最近 {len(entries)} 轮："]
    for i, e in enumerate(entries, 1):
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.ts))
        lines.append(f"── 最近第 {i} 轮 · {when} · {e.iterations} 次迭代")
        lines.append(e.summary)
    return "\n".join(lines)
