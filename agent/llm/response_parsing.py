"""聊天响应解析：litellm 流式增量聚合、tool_calls 缓冲合并与 ChatResult 构建。"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncGenerator, Dict, Optional

from agent.llm.types import ChatResult, ChatStreamDelta, ToolCall, UsageInfo
from core.log import log

_THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


async def _iter_stream(
    stream: Any,
    reasoning_buf: str,
    tc_bufs: Dict[int, Dict[str, str]],
) -> AsyncGenerator[tuple[ChatStreamDelta, str], None]:
    """解析 LiteLLM 流，并保留跨 chunk 的工具与推理缓冲。"""
    async for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            stream_usage = _usage_from_object(getattr(chunk, "usage", None))
            if stream_usage:
                yield ChatStreamDelta(usage=stream_usage), reasoning_buf
            continue
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        content = getattr(delta, "content", None) or ""
        finish = getattr(choice, "finish_reason", None) or ""
        reasoning = ""
        rc = getattr(delta, "reasoning_content", None)
        if isinstance(rc, str) and rc:
            reasoning = rc
        else:
            for detail in getattr(delta, "reasoning_details", None) or []:
                text = detail.get("text", "") if isinstance(detail, dict) else getattr(detail, "text", "")
                if text and len(text) > len(reasoning_buf):
                    reasoning = text[len(reasoning_buf):]
                    reasoning_buf = text

        for tc_chunk in getattr(delta, "tool_calls", None) or []:
            # 部分 provider 会把 index 返回为字符串，统一强转 int，
            # 避免混合类型 key 在 sorted() 时炸 TypeError
            idx = _normalize_tc_index(
                getattr(tc_chunk, "index", None), len(tc_bufs)
            )
            buf = tc_bufs.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            tc_id = getattr(tc_chunk, "id", None)
            if tc_id:
                buf["id"] = tc_id
            func = getattr(tc_chunk, "function", None)
            if func:
                if getattr(func, "name", None):
                    buf["name"] = func.name
                arguments = getattr(func, "arguments", None)
                if arguments:
                    buf["arguments"] += str(arguments)

        completed_tools = (
            _complete_tool_buffers(tc_bufs)
            if finish in ("tool_calls", "stop") and tc_bufs
            else []
        )
        if completed_tools:
            tc_bufs.clear()
        elif finish == "length" and tc_bufs:
            log(
                f"流式输出达到长度上限，丢弃 {len(tc_bufs)} 个不完整的 tool_call 缓冲",
                "WARNING", tag="模型",
            )
            tc_bufs.clear()
        yield ChatStreamDelta(
            content=content,
            tool_calls=completed_tools,
            finish_reason=finish,
            reasoning_content=reasoning,
            # usage 仅在最终 chunk 输出：带 finish 的 chunk 或无 choices 的
            # usage-only chunk（上方分支），中间 chunk 的增量 usage 不下发
            usage=_usage_from_object(getattr(chunk, "usage", None)) if finish else None,
        ), reasoning_buf

@staticmethod
def _normalize_tc_index(raw: Any, fallback: int) -> int:
    """将流式 tool_call 的 index 归一化为 int，非法值回退为 fallback。"""
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback

@staticmethod
def _complete_tool_buffers(
    tc_bufs: Dict[int, Dict[str, str]],
) -> list[ToolCall]:
    result: list[ToolCall] = []
    for _, buf in sorted(tc_bufs.items()):
        if not buf["name"]:
            log(
                f"丢弃无名流式 tool_call 片段: id={buf['id']} args={buf['arguments'][:120]}",
                "WARNING", tag="模型",
            )
            continue
        call_id = buf["id"] or f"tc_{len(result)}"
        result.append(ToolCall(
            id=call_id,
            name=buf["name"],
            arguments=buf["arguments"],
            # raw 必须是 OpenAI 线格式完整结构 — think_loop 用它拼装
            # assistant 历史消息（tool_calls 字段），缺 id 会破坏配对
            raw={
                "id": call_id,
                "type": "function",
                "function": {"name": buf["name"], "arguments": buf["arguments"]},
            },
        ))
    return result

# ------------------------------------------------------------------
# 响应解析
# ------------------------------------------------------------------

def _parse_response(resp: Any) -> ChatResult:
    """将 litellm 统一响应解析为 ChatResult（含 usage）。"""
    choices = getattr(resp, "choices", None) or []
    raw_dict: Optional[dict] = resp.model_dump() if hasattr(resp, "model_dump") else None
    choice = choices[0] if choices else None
    msg = getattr(choice, "message", None) if choice is not None else None
    if msg is None:
        finish = (getattr(choice, "finish_reason", None) if choice else None) or "error"
        return ChatResult(
            content="",
            finish_reason=finish,
            raw=raw_dict,
            usage=_extract_usage(resp),
            model=getattr(resp, "model", "") or "",
        )

    usage = _extract_usage(resp)

    return ChatResult(
        content=msg.content or "",
        tool_calls=_parse_tool_calls(getattr(msg, "tool_calls", None)),
        finish_reason=getattr(choice, "finish_reason", None) or "",
        reasoning_content=_extract_reasoning(msg, raw_dict),
        raw=raw_dict,
        usage=usage,
        model=getattr(resp, "model", "") or "",
    )

@staticmethod
def _extract_usage(resp: Any) -> Optional[UsageInfo]:
    """从 litellm 响应中提取 token 用量。"""
    return _usage_from_object(getattr(resp, "usage", None))

@staticmethod
def _usage_from_object(usage: Any) -> Optional[UsageInfo]:
    if not usage:
        return None
    result = UsageInfo(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
    )
    return result if result.total_tokens or result.prompt_tokens or result.completion_tokens else None

@staticmethod
def _extract_reasoning(msg: Any, raw_response: Optional[dict] = None) -> str:
    """从响应中提取推理内容。

    支持三种来源（按优先级）：
    1. reasoning_content 字段（litellm 标准，Anthropic thinking blocks）
    2. reasoning_details 字段（自定义累积格式）
    3. <think> 标签（DeepSeek 等模型）
    """
    rc = getattr(msg, "reasoning_content", None)
    if rc and isinstance(rc, str):
        return rc

    details = getattr(msg, "reasoning_details", None)
    if not details and raw_response:
        choices = raw_response.get("choices", [])
        if choices:
            msg_dict = choices[0].get("message", {})
            if msg_dict.get("reasoning_content"):
                return str(msg_dict["reasoning_content"])
            details = msg_dict.get("reasoning_details")

    if details:
        parts: list[str] = []
        for d in details:
            text = d.get("text", "") if isinstance(d, dict) else getattr(d, "text", "")
            if text:
                parts.append(text)
        if parts:
            return "\n".join(parts)

    content = getattr(msg, "content", "") or ""
    m = _THINK_TAG_RE.search(content)
    return m.group(1).strip() if m else ""

@staticmethod
def _parse_tool_calls(raw_tool_calls: Any) -> list[ToolCall]:
    """解析 tool_calls，兼容 OpenAI 对象 / dict / anthropic tool_use 原始形态。

    部分 anthropic 桥接端点返回的 tool_call 结构不标准（function 为 dict、
    或 name 位于顶层），此处做多形态兜底；名字缺失的调用丢弃并输出原始
    结构日志，避免空调用在思维循环中反复失败。
    """
    if not raw_tool_calls:
        return []
    result: list[ToolCall] = []
    for i, tc in enumerate(raw_tool_calls):
        raw_dict: dict = {}
        if hasattr(tc, "model_dump"):
            try:
                raw_dict = tc.model_dump()
            except Exception:
                raw_dict = {}
        elif isinstance(tc, dict):
            raw_dict = tc

        func = getattr(tc, "function", None)
        if func is None and isinstance(tc, dict):
            func = tc.get("function")

        func_name: Any = None
        func_args: Any = None
        if isinstance(func, dict):
            func_name = func.get("name")
            func_args = func.get("arguments")
        elif func is not None:
            func_name = getattr(func, "name", None)
            func_args = getattr(func, "arguments", None)
        else:
            # anthropic tool_use 原始形态：{type: "tool_use", name, input}
            func_name = raw_dict.get("name")
            func_args = raw_dict.get("input") if "input" in raw_dict else raw_dict.get("arguments")

        name = func_name.strip() if isinstance(func_name, str) else ""
        if not name:
            log(
                f"丢弃无名 tool_call（端点返回结构异常）: {json.dumps(raw_dict, ensure_ascii=False)[:300]}",
                "WARNING", tag="模型",
            )
            continue

        args_str = func_args if isinstance(func_args, str) else json.dumps(
            func_args if func_args is not None else {}, ensure_ascii=False,
        )
        tc_id = getattr(tc, "id", None) or raw_dict.get("id") or f"tc_{i}"
        result.append(ToolCall(
            id=str(tc_id),
            name=name,
            arguments=args_str,
            raw=raw_dict,
        ))
    return result
