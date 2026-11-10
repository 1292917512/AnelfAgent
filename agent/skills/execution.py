"""执行内的技能阅读事实与显式工具前置约束。

Model Experience: 关联工具缺少完整技能正文时返回未执行及读取指引；读取后重新决策。
Token effect: 未关联工具零增量，缺失时一条有界错误；不增加模型判断调用。
Cache effect: 事实只存本次执行，正文通过已有动态注入或工具结果提供，不改变稳定前缀。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from agent.skills.skill_store import SkillStore
from core.tool_errors import ErrorCause, tool_error
from core.tool_results import PresentedToolResult, parse_tool_result_json


@dataclass
class _Reading:
    contents: dict[str, str] = field(default_factory=dict)


_reading: ContextVar[_Reading | None] = ContextVar("skill_reading", default=None)


@contextmanager
def skill_execution() -> Iterator[None]:
    """主执行、子执行和工作流分别建立读取范围，子任务不借用父执行事实。"""
    token = _reading.set(_Reading())
    try:
        yield
    finally:
        _reading.reset(token)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def skill_result(output: str, name: str, content: str) -> PresentedToolResult:
    """复用展示回执，仅在最终结果完整保留该正文时登记读取事实。"""
    reading = _reading.get()
    digest = content_hash(content)

    def acknowledge(visible: str) -> None:
        if reading is None:
            return
        payload = parse_tool_result_json(visible)
        if isinstance(payload, dict) and payload.get("name") == name and payload.get("content") == content:
            reading.contents[name] = digest

    return PresentedToolResult(output, acknowledge)


def observe_messages(messages: list[dict[str, Any]]) -> None:
    """发送前核对显式动态注入的实际正文范围，截断或改写不能产生完整读取事实。"""
    reading = _reading.get()
    if reading is None:
        return
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        for fact in message.get("_source", {}).get("skills", []):
            if fact.get("mode") != "read" or fact.get("truncated"):
                continue
            start, end = fact.get("body_start"), fact.get("body_end")
            if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(content):
                digest = content_hash(content[start:end])
                if digest == fact.get("content_hash"):
                    reading.contents[fact["name"]] = digest


def check_required_skills(store: SkillStore, tool_name: str) -> str | None:
    """按确切工具名查显式关联，以当前正文核对已交付版本；不根据相关性猜测门槛。"""
    reading = _reading.get()
    missing: list[str] = []
    for name in store.required_skills(tool_name):
        skill = store.get(name)
        if skill is None or reading is None or reading.contents.get(name) != content_hash(skill.content):
            missing.append(name)
    if not missing:
        return None
    return tool_error(
        "工具前置技能尚未完整读取，本次工具未执行", cause=ErrorCause.STATE, retryable=False,
        hint="先用 get_skill 完整读取 required_skills，再依据技能重新决定是否调用；不得改用其他通道绕过。",
        required_skills=missing, outcome="not_started",
    )
