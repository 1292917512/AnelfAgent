"""自动判断的有界会话证据。

模型看到：按调用场景提取对话或执行证据，保留身份标签和摘录标记；自动召回只取对话。
Token：共享字符预算，排除系统提示、多媒体载荷及重复正文，不另调用模型摘要。
缓存：只生成判断输入，不修改主模型上下文或历史前缀。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from agent.judgment.types import JsonValue
from core.config import get_config_int
from core.sanitizer import sanitize_text, truncate_middle


def auto_input_budget() -> int:
    """自动判断的事实总预算；显式 judge 与 SDK 调用不受影响。"""
    return max(2000, min(32000, get_config_int("judgment_auto_max_chars", 8000)))


def evidence_json(value: JsonValue) -> str:
    """编码脱敏事实，保留结构并使用紧凑 JSON 计量预算。"""
    return sanitize_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _origin(message: Mapping[str, Any]) -> str:
    source = message.get("_source")
    return str(source.get("origin", "")) if isinstance(source, Mapping) else ""


def _content_text(content: Any) -> str:
    """只提取正文文本块，不序列化媒体载荷或思考块。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content
                         if isinstance(block, dict) and block.get("type") in {"text", "input_text", "output_text"}
                         and isinstance(block.get("text"), str))
    return ""


def dialogue_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """提取按原顺序排列的对话正文及归属，排除注入、工具执行和内部思考。"""
    result: list[dict[str, Any]] = []
    for message in messages:
        if (message.get("role") not in {"user", "assistant"}
                or message.get("_layer") not in {None, "", "conversation"}
                or _origin(message) not in {"", "conversation", "steer"}
                or message.get("tool_calls")):
            continue
        content = _content_text(message.get("content")).strip()
        if content:
            result.append({"role": message["role"], "content": content,
                           **{key: message[key] for key in ("_layer", "_source") if key in message}})
    return result


def _text(message: Mapping[str, Any], limit: int) -> tuple[str, bool]:
    """提取文本块与工具名称、参数摘录，不序列化媒体或整份调用对象。"""
    parts = [_content_text(message.get("content"))]
    calls = message.get("tool_calls")
    shortened = False
    if isinstance(calls, list):
        for call in calls[:4]:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments")
            preview = truncate_middle(arguments, 200) if isinstance(arguments, str) else ""
            shortened |= isinstance(arguments, str) and len(arguments) > 200
            parts.append(f"{str(function.get('name', ''))[:120]}({preview})")
        shortened |= len(calls) > 4
    text = "\n".join(parts).strip()
    shortened |= len(text) > limit
    return sanitize_text(truncate_middle(text, limit)), shortened


def conversation_evidence(
    messages: Sequence[Mapping[str, Any]], *, max_chars: int | None = None,
    include_delivered: bool = False, exclude_content: str = "",
) -> list[JsonValue]:
    """在共享预算内优先保留最新用户请求与送达事实，再按时间还原近期执行。"""
    configured = max(600, min(12000, get_config_int("judgment_evidence_max_chars", 3000)))
    budget = configured if max_chars is None else min(configured, max_chars)
    if budget < 160:
        return []
    indices: list[int] = []
    user_index: int | None = None
    delivered: list[int] = []
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        role, origin = message.get("role"), _origin(message)
        if (role not in {"user", "assistant", "tool"}
                or message.get("_layer") not in {None, "", "conversation"}
                or origin not in {"", "conversation", "steer"}):
            continue
        if not message.get("content") and not message.get("tool_calls"):
            continue
        sent = role == "assistant" and origin == "conversation"
        if exclude_content and not sent and role == "assistant" and message.get("content") == exclude_content:
            continue
        if role == "user" and user_index is None:
            user_index = index
        if sent and len(delivered) < 3:
            delivered.append(index)
        indices.append(index)
    priority = ([user_index] if user_index is not None else []) + (delivered if include_delivered else []) + indices
    selected: dict[int, JsonValue] = {}
    used = 2
    for index in dict.fromkeys(priority):
        if len(selected) == 6:
            break
        remaining = budget - used
        if remaining < 160:
            break
        message = messages[index]
        allowance = min(1800, max(160, budget * 3 // 10 if index == user_index else budget // 6))
        text, shortened = _text(message, min(allowance, remaining - 100))
        if not text:
            continue
        item: dict[str, JsonValue] = {"role": str(message["role"]), "content": text}
        if include_delivered and message["role"] == "assistant" and _origin(message) == "conversation":
            item["delivered"] = True
        if shortened:
            item["truncated"] = True
        size = len(evidence_json(item)) + 1
        if size > remaining:
            # JSON 转义也计入预算，不能用原文字符数低估实际输入。
            item["content"] = truncate_middle(text, max(0, len(text) - (size - remaining) - 20))
            item["truncated"] = True
            size = len(evidence_json(item)) + 1
        if size <= remaining:
            selected[index] = item
            used += size
    return [selected[index] for index in sorted(selected)]
