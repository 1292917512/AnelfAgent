"""Responses API 结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from agent.llm.types import ChatResult, ToolCall, UsageInfo


@dataclass(slots=True)
class ResponseUsage:
    """Responses usage（兼容 Chat Completions 字段名）。"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    def to_usage_info(self) -> UsageInfo:
        return UsageInfo(
            prompt_tokens=self.input_tokens,
            completion_tokens=self.output_tokens,
            total_tokens=self.total_tokens or (self.input_tokens + self.output_tokens),
        )


@dataclass(slots=True)
class ResponseStreamEvent:
    """规范化后的 Responses SSE 事件。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    is_terminal: bool = False


@dataclass(slots=True)
class ResponseResult:
    """统一 Responses 结果，可转换回 ChatResult。"""

    id: str = ""
    status: str = ""
    model: str = ""
    output_text: str = ""
    reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    output: list[dict[str, Any]] = field(default_factory=list)
    usage: Optional[ResponseUsage] = None
    error: Optional[dict[str, Any]] = None
    previous_response_id: str = ""
    transport: str = ""
    raw: Optional[dict[str, Any]] = None

    def to_chat_result(self) -> ChatResult:
        finish = "tool_calls" if self.tool_calls else (
            "error" if self.status in {"failed", "cancelled", "incomplete"} else "stop"
        )
        return ChatResult(
            content=self.output_text,
            tool_calls=list(self.tool_calls),
            finish_reason=finish,
            reasoning_content=self.reasoning_content,
            raw=self.raw,
            usage=self.usage.to_usage_info() if self.usage else None,
            model=self.model,
        )


TERMINAL_EVENT_TYPES = frozenset({
    "response.completed",
    "response.failed",
    "response.incomplete",
    "error",
    "response.error",
})


def event_is_terminal(event_type: str) -> bool:
    return event_type in TERMINAL_EVENT_TYPES
