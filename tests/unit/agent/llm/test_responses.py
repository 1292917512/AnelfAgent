"""Responses 协议单元测试：路由能力矩阵 / SessionStore / chat 桥接。"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.llm.llm_client import LLMClient, LLMClientConfig
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
from agent.llm.responses.session import ResponseSessionStore
from agent.llm.responses.types import ResponseResult, ResponseUsage, event_is_terminal

# ==================================================================
# 路由与能力矩阵
# ==================================================================

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


# ==================================================================
# SessionStore
# ==================================================================

@pytest.mark.asyncio
async def test_session_create_complete_get_delete() -> None:
    store = ResponseSessionStore(ttl_seconds=60)
    session = await store.create(
        model_id="gpt-4o",
        provider_id="openai",
        api_type="openai",
        api_base="https://api.openai.com/v1",
        transport="native",
    )
    assert session.response_id.startswith("resp_")

    result = ResponseResult(id=session.response_id, status="completed", model="gpt-4o")
    await store.complete(session.response_id, result=result)
    loaded = await store.require(session.response_id, provider_id="openai")
    assert loaded.result is not None
    assert loaded.result.status == "completed"

    assert await store.delete(session.response_id) is True
    assert await store.get(session.response_id) is None


@pytest.mark.asyncio
async def test_session_provider_binding() -> None:
    store = ResponseSessionStore()
    session = await store.create(
        model_id="MiniMax",
        provider_id="minimax",
        api_type="openai",
        api_base="https://api.minimax.chat/v1",
        transport="bridge",
    )
    with pytest.raises(PermissionError):
        await store.require(session.response_id, provider_id="openai")
    with pytest.raises(PermissionError):
        await store.require(
            session.response_id,
            provider_id="minimax",
            api_base="https://other.example/v1",
        )


@pytest.mark.asyncio
async def test_session_cancel_stops_task() -> None:
    store = ResponseSessionStore()

    async def _hang() -> None:
        await asyncio.sleep(30)

    task = asyncio.create_task(_hang())
    session = await store.create(
        model_id="gpt-4o",
        provider_id="openai",
        api_type="openai",
        api_base="https://api.openai.com/v1",
        transport="native",
        task=task,
    )
    cancelled = await store.cancel(session.response_id)
    assert cancelled.status == "cancelled"
    assert task.cancelled() or task.done()


# ==================================================================
# chat_protocol 与 responses 桥接
# ==================================================================

def test_chat_protocol_validation() -> None:
    with pytest.raises(ValueError, match="chat_protocol"):
        LLMClientConfig(chat_protocol="websocket")
    cfg = LLMClientConfig(chat_protocol="auto", api_type="openai")
    client = LLMClient(cfg)
    assert client.resolved_chat_protocol == ChatProtocol.RESPONSES


@pytest.mark.asyncio
async def test_chat_bridges_to_responses_when_configured() -> None:
    client = LLMClient(LLMClientConfig(
        model="gpt-4o",
        api_type="openai",
        base_url="https://api.openai.com/v1",
        chat_protocol="responses",
    ))
    client.responses_create = AsyncMock(return_value=ResponseResult(  # type: ignore[method-assign]
        id="resp_x",
        status="completed",
        model="gpt-4o",
        output_text="bridged",
        usage=ResponseUsage(input_tokens=1, output_tokens=1, total_tokens=2),
    ))

    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.content == "bridged"
    client.responses_create.assert_awaited_once()
    kwargs: dict[str, Any] = client.responses_create.await_args.kwargs
    assert kwargs["input"] == "hi"
