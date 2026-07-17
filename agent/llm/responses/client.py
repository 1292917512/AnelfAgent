"""Responses API 客户端封装（基于 litellm.aresponses）。"""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import litellm

from agent.llm.protocol import TransportMode
from agent.llm.responses.router import (
    ResponsesCapabilityError,
    ResponsesRoute,
    require_operation,
    resolve_responses_route,
    validate_tools_for_route,
)
from agent.llm.responses.types import (
    ResponseResult,
    ResponseStreamEvent,
    ResponseUsage,
    event_is_terminal,
)
from agent.llm.types import ToolCall
from core.log import debug


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        dumped = obj.model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if hasattr(obj, "dict"):
        dumped = obj.dict()
        return dumped if isinstance(dumped, dict) else {}
    data: dict[str, Any] = {}
    for key in (
        "id", "status", "model", "output", "usage", "error",
        "previous_response_id", "type", "content", "arguments",
        "call_id", "name", "text", "summary",
    ):
        if hasattr(obj, key):
            data[key] = getattr(obj, key)
    return data


def _extract_usage(raw: dict[str, Any]) -> Optional[ResponseUsage]:
    usage = raw.get("usage")
    if not usage:
        return None
    if not isinstance(usage, dict):
        usage = _as_dict(usage)
    input_tokens = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or 0
    )
    output_tokens = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or 0
    )
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    return ResponseUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        raw=usage,
    )


def _tool_call_from_item(item: dict[str, Any]) -> Optional[ToolCall]:
    item_type = str(item.get("type", ""))
    if item_type not in {"function_call", "custom_tool_call"}:
        return None
    name = str(item.get("name") or "")
    if not name:
        return None
    arguments = item.get("arguments", "")
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments, ensure_ascii=False)
    call_id = str(item.get("call_id") or item.get("id") or "")
    return ToolCall(
        id=call_id or f"fc_{name}",
        name=name,
        arguments=arguments,
        raw={
            "id": call_id or f"fc_{name}",
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        },
    )


def _extract_text_and_tools(output: list[Any]) -> tuple[str, str, list[ToolCall]]:
    texts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for raw_item in output:
        item = raw_item if isinstance(raw_item, dict) else _as_dict(raw_item)
        item_type = str(item.get("type", ""))
        if item_type == "message":
            for part in item.get("content") or []:
                part_dict = part if isinstance(part, dict) else _as_dict(part)
                if part_dict.get("type") in {"output_text", "text"}:
                    text = part_dict.get("text") or part_dict.get("output_text") or ""
                    if text:
                        texts.append(str(text))
        elif item_type == "reasoning":
            summary = item.get("summary")
            if isinstance(summary, list):
                for part in summary:
                    part_dict = part if isinstance(part, dict) else _as_dict(part)
                    text = part_dict.get("text") or ""
                    if text:
                        reasoning_parts.append(str(text))
            content = item.get("content")
            if isinstance(content, str) and content:
                reasoning_parts.append(content)
        else:
            tool = _tool_call_from_item(item)
            if tool is not None:
                tool_calls.append(tool)
    return "\n".join(texts), "\n".join(reasoning_parts), tool_calls


def parse_responses_payload(
    payload: Any,
    *,
    transport: str = "",
) -> ResponseResult:
    """将 litellm Responses 响应解析为 ResponseResult。"""
    raw = _as_dict(payload)
    output_items = raw.get("output") or []
    if not isinstance(output_items, list):
        output_items = []
    normalized_output = [
        item if isinstance(item, dict) else _as_dict(item)
        for item in output_items
    ]
    text, reasoning, tool_calls = _extract_text_and_tools(normalized_output)
    if not text and isinstance(raw.get("output_text"), str):
        text = raw["output_text"]
    status = str(raw.get("status") or ("failed" if raw.get("error") else "completed"))
    return ResponseResult(
        id=str(raw.get("id") or ""),
        status=status,
        model=str(raw.get("model") or ""),
        output_text=text,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
        output=normalized_output,
        usage=_extract_usage(raw),
        error=raw.get("error") if isinstance(raw.get("error"), dict) else None,
        previous_response_id=str(raw.get("previous_response_id") or ""),
        transport=transport,
        raw=raw,
    )


def normalize_stream_event(event: Any) -> ResponseStreamEvent:
    data = _as_dict(event)
    event_type = str(data.get("type") or getattr(event, "type", "") or "generic")
    return ResponseStreamEvent(
        type=event_type,
        data=data,
        is_terminal=event_is_terminal(event_type),
    )


def messages_to_responses_input(
    messages: list[dict[str, Any]],
) -> tuple[str, Union[str, list[dict[str, Any]]]]:
    """将 Chat Completions messages 转为 Responses input/instructions。"""
    instructions_parts: list[str] = []
    input_items: list[dict[str, Any]] = []

    for message in messages:
        role = str(message.get("role") or "")
        content = message.get("content", "")
        if role == "system":
            if isinstance(content, str) and content:
                instructions_parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text")
                        if text:
                            instructions_parts.append(str(text))
            continue

        if role == "tool":
            input_items.append({
                "type": "function_call_output",
                "call_id": message.get("tool_call_id") or "",
                "output": content if isinstance(content, str) else json.dumps(
                    content, ensure_ascii=False,
                ),
            })
            continue

        if role == "assistant" and message.get("tool_calls"):
            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function") or {}
                input_items.append({
                    "type": "function_call",
                    "id": tool_call.get("id") or "",
                    "call_id": tool_call.get("id") or "",
                    "name": function.get("name") or "",
                    "arguments": function.get("arguments") or "",
                })
            if content:
                input_items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": content if not isinstance(content, str) else [
                        {"type": "output_text", "text": content},
                    ],
                })
            continue

        if role in {"user", "assistant"}:
            if isinstance(content, str):
                input_items.append({
                    "role": role,
                    "content": content,
                })
            else:
                input_items.append({
                    "type": "message",
                    "role": role,
                    "content": content,
                })

    instructions = "\n\n".join(part for part in instructions_parts if part)
    if len(input_items) == 1 and isinstance(input_items[0].get("content"), str):
        return instructions, str(input_items[0]["content"])
    return instructions, input_items


def convert_chat_tools(tools: Optional[list[dict[str, Any]]]) -> Optional[list[dict[str, Any]]]:
    """将 Chat Completions tools 转为 Responses function tools。"""
    if not tools:
        return None
    converted: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            function = tool["function"]
            converted.append({
                "type": "function",
                "name": function.get("name") or "",
                "description": function.get("description") or "",
                "parameters": function.get("parameters") or {"type": "object", "properties": {}},
            })
        else:
            converted.append(tool)
    return converted


class ResponsesClient:
    """面向单个 LLMClientConfig 的 Responses 调用封装。"""

    def __init__(
        self,
        *,
        model: str,
        api_type: str,
        api_base: str = "",
        api_key: str = "",
        timeout: float = 120.0,
        request_params: Optional[dict[str, Any]] = None,
        extra_body: Optional[dict[str, Any]] = None,
        prefer_bridge_for_custom: bool = True,
        http_client: Any = None,
    ) -> None:
        self.model = model
        self.api_type = api_type
        self.api_base = api_base
        self.api_key = api_key
        self.timeout = timeout
        self.request_params = dict(request_params or {})
        self.extra_body = dict(extra_body or {})
        self.http_client = http_client
        self.route = resolve_responses_route(
            api_type=api_type,
            api_base=api_base,
            prefer_bridge_for_custom=prefer_bridge_for_custom,
        )

    def ensure_create_supported(self, tools: Optional[list[Any]] = None) -> ResponsesRoute:
        if self.route.transport == TransportMode.UNSUPPORTED:
            raise ResponsesCapabilityError(
                f"当前 provider ({self.api_type}) 不支持 Responses API"
            )
        require_operation(self.route, "create")
        validate_tools_for_route(self.route, tools)
        return self.route

    def _base_kwargs(self) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "timeout": self.timeout,
            "custom_llm_provider": self.api_type if self.api_type != "openai" else None,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.http_client is not None:
            kwargs["http_client"] = self.http_client
        if self.route.force_chat_completions_api:
            kwargs["use_chat_completions_api"] = True
        kwargs.update(self.request_params)
        if self.extra_body:
            kwargs["extra_body"] = dict(self.extra_body)
        # custom_llm_provider=None 时删掉，避免 litellm 误判
        if kwargs.get("custom_llm_provider") is None:
            kwargs.pop("custom_llm_provider", None)
        return kwargs

    async def create(
        self,
        *,
        input: Union[str, list[dict[str, Any]]],
        instructions: str = "",
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        previous_response_id: str = "",
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
        store: Optional[bool] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> ResponseResult:
        self.ensure_create_supported(tools)
        if previous_response_id:
            require_operation(self.route, "previous_response_id")
        kwargs = self._base_kwargs()
        kwargs["input"] = input
        if instructions:
            kwargs["instructions"] = instructions
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        if metadata is not None:
            kwargs["metadata"] = metadata
        if store is not None:
            kwargs["store"] = store
        if extra:
            kwargs.update(extra)
        debug(
            f"Responses create: model={self.model}, transport={self.route.transport.value}",
            tag="模型",
        )
        resp = await litellm.aresponses(**kwargs)
        return parse_responses_payload(resp, transport=self.route.transport.value)

    async def stream(
        self,
        *,
        input: Union[str, list[dict[str, Any]]],
        instructions: str = "",
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Optional[Any] = None,
        previous_response_id: str = "",
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_output_tokens: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
        store: Optional[bool] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> AsyncGenerator[ResponseStreamEvent, None]:
        self.ensure_create_supported(tools)
        require_operation(self.route, "stream")
        if previous_response_id:
            require_operation(self.route, "previous_response_id")
        kwargs = self._base_kwargs()
        kwargs.update({
            "input": input,
            "stream": True,
        })
        if instructions:
            kwargs["instructions"] = instructions
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if temperature is not None:
            kwargs["temperature"] = temperature
        if top_p is not None:
            kwargs["top_p"] = top_p
        if max_output_tokens is not None:
            kwargs["max_output_tokens"] = max_output_tokens
        if metadata is not None:
            kwargs["metadata"] = metadata
        if store is not None:
            kwargs["store"] = store
        if extra:
            kwargs.update(extra)

        stream = await litellm.aresponses(**kwargs)
        saw_terminal = False
        try:
            async for event in stream:  # type: ignore[union-attr]
                normalized = normalize_stream_event(event)
                if normalized.is_terminal:
                    saw_terminal = True
                yield normalized
        finally:
            close_fn = getattr(stream, "aclose", None)
            if close_fn:
                await close_fn()
        if not saw_terminal:
            raise RuntimeError("Responses 流在缺少终态事件时结束")

    async def get(self, response_id: str) -> ResponseResult:
        require_operation(self.route, "retrieve")
        kwargs = self._base_kwargs()
        kwargs.pop("model", None)
        resp = await litellm.aget_responses(response_id=response_id, **kwargs)
        return parse_responses_payload(resp, transport=self.route.transport.value)

    async def delete(self, response_id: str) -> dict[str, Any]:
        require_operation(self.route, "delete")
        kwargs = self._base_kwargs()
        kwargs.pop("model", None)
        resp = await litellm.adelete_responses(response_id=response_id, **kwargs)
        return _as_dict(resp)

    async def cancel(self, response_id: str) -> ResponseResult:
        require_operation(self.route, "cancel")
        kwargs = self._base_kwargs()
        kwargs.pop("model", None)
        resp = await litellm.acancel_responses(response_id=response_id, **kwargs)
        return parse_responses_payload(resp, transport=self.route.transport.value)

    async def compact(
        self,
        *,
        input: Union[str, list[dict[str, Any]]],
        instructions: str = "",
        previous_response_id: str = "",
        extra: Optional[dict[str, Any]] = None,
    ) -> ResponseResult:
        require_operation(self.route, "compact")
        kwargs = self._base_kwargs()
        kwargs["input"] = input
        if instructions:
            kwargs["instructions"] = instructions
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if extra:
            kwargs.update(extra)
        resp = await litellm.acompact_responses(**kwargs)
        return parse_responses_payload(resp, transport=self.route.transport.value)
