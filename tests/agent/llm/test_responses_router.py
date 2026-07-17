"""Responses 路由与能力矩阵契约测试。"""

from __future__ import annotations

import pytest

from agent.llm.protocol import ChatProtocol, TransportMode, resolve_chat_protocol
from agent.llm.responses.client import (
    convert_chat_tools,
    messages_to_responses_input,
    parse_responses_payload,
)
from agent.llm.responses.router import (
    ResponsesCapabilityError,
    require_operation,
    resolve_responses_route,
    validate_tools_for_route,
)
from agent.llm.responses.types import event_is_terminal


def test_native_openai_route() -> None:
    route = resolve_responses_route(
        api_type="openai",
        api_base="https://api.openai.com/v1",
    )
    assert route.transport == TransportMode.NATIVE
    assert route.force_chat_completions_api is False


def test_custom_openai_compatible_defaults_to_bridge() -> None:
    route = resolve_responses_route(
        api_type="openai",
        api_base="https://api.minimax.chat/v1",
    )
    assert route.transport == TransportMode.BRIDGE
    assert route.force_chat_completions_api is True


def test_anthropic_bridge_and_unsupported_lifecycle() -> None:
    route = resolve_responses_route(api_type="anthropic")
    assert route.transport == TransportMode.BRIDGE
    require_operation(route, "create")
    with pytest.raises(ResponsesCapabilityError, match="retrieve"):
        require_operation(route, "retrieve")
    with pytest.raises(ResponsesCapabilityError, match="compact"):
        require_operation(route, "compact")


def test_builtin_tools_only_on_native() -> None:
    native = resolve_responses_route(
        api_type="openai",
        api_base="https://api.openai.com/v1",
    )
    validate_tools_for_route(native, [{"type": "web_search"}])

    bridge = resolve_responses_route(api_type="anthropic")
    with pytest.raises(ResponsesCapabilityError, match="内置工具"):
        validate_tools_for_route(bridge, [{"type": "web_search"}])


def test_auto_protocol_prefers_native_openai() -> None:
    assert resolve_chat_protocol("auto", api_type="openai") == ChatProtocol.RESPONSES
    assert resolve_chat_protocol("auto", api_type="anthropic") == ChatProtocol.CHAT_COMPLETIONS


def test_messages_and_tools_mapping() -> None:
    instructions, payload = messages_to_responses_input([
        {"role": "system", "content": "you are helpful"},
        {"role": "user", "content": "hello"},
    ])
    assert instructions == "you are helpful"
    assert payload == "hello"

    tools = convert_chat_tools([{
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "d",
            "parameters": {"type": "object", "properties": {}},
        },
    }])
    assert tools is not None
    assert tools[0]["type"] == "function"
    assert tools[0]["name"] == "lookup"


def test_parse_responses_payload_to_chat_result() -> None:
    result = parse_responses_payload({
        "id": "resp_1",
        "status": "completed",
        "model": "gpt-4o",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "hi"}],
            },
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "{\"q\":\"a\"}",
            },
        ],
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }, transport="native")
    chat = result.to_chat_result()
    assert chat.content == "hi"
    assert chat.finish_reason == "tool_calls"
    assert chat.tool_calls[0].name == "lookup"
    assert chat.usage is not None
    assert chat.usage.total_tokens == 5


def test_terminal_event_validation() -> None:
    assert event_is_terminal("response.completed")
    assert event_is_terminal("error")
    assert not event_is_terminal("response.output_text.delta")
