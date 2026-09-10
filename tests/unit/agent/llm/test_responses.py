"""Responses 协议单元测试：路由能力矩阵 / SessionStore / chat 桥接。"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.llm.llm_client import LLMClient, LLMClientConfig
from agent.llm.protocol import ChatProtocol, TransportMode, resolve_chat_protocol
from agent.llm.responses.client import (
    convert_chat_tools,
    messages_to_responses_input,
    normalize_stream_event,
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

def test_openai_route_is_native() -> None:
    """openai 系一律 native 直连 /responses（显式 responses = 绝对官方接口）。"""
    route = resolve_responses_route(api_type="openai")
    assert route.transport == TransportMode.NATIVE
    assert route.force_chat_completions_api is False
    require_operation(route, "retrieve")


def test_anthropic_bridge_and_unsupported_lifecycle() -> None:
    route = resolve_responses_route(api_type="anthropic")
    assert route.transport == TransportMode.BRIDGE
    require_operation(route, "create")
    with pytest.raises(ResponsesCapabilityError, match="retrieve"):
        require_operation(route, "retrieve")
    with pytest.raises(ResponsesCapabilityError, match="compact"):
        require_operation(route, "compact")


def test_builtin_tools_only_on_native() -> None:
    native = resolve_responses_route(api_type="openai")
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


def test_normalize_stream_event_enum_type() -> None:
    """OpenAI SDK 流事件的 str-enum type 必须取 value，否则终态事件无法识别。"""

    class FakeEventType(str, Enum):
        COMPLETED = "response.completed"

    class FakeEvent:
        type = FakeEventType.COMPLETED

        def model_dump(self) -> dict[str, Any]:
            return {"type": self.type, "response": {"id": "r1"}}

    event = normalize_stream_event(FakeEvent())
    assert event.type == "response.completed"
    assert event.is_terminal is True


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


def _not_found() -> Exception:
    import litellm

    return litellm.NotFoundError(
        message="404 page not found", model="k3", llm_provider="openai",
    )


def test_responses_fallback_gate() -> None:
    """404 回退门控：仅 auto + native 通道命中，显式 responses/bridge 不回退。"""
    auto_client = LLMClient(LLMClientConfig(
        model="k3", api_type="openai",
        base_url="https://api.kimi.com/coding/v1", chat_protocol="auto",
    ))
    assert auto_client._should_fallback_from_responses(_not_found()) is True
    assert auto_client._responses_native_blocked is True
    # 非 404 错误不回退
    other = LLMClient(LLMClientConfig(
        model="k3", api_type="openai",
        base_url="https://api.kimi.com/coding/v1", chat_protocol="auto",
    ))
    assert other._should_fallback_from_responses(ValueError("boom")) is False
    assert other._responses_native_blocked is False
    # 显式 responses = 绝对 native，不回退
    explicit = LLMClient(LLMClientConfig(
        model="k3", api_type="openai",
        base_url="https://api.kimi.com/coding/v1", chat_protocol="responses",
    ))
    assert explicit._should_fallback_from_responses(_not_found()) is False
    # bridge 通道（anthropic）不回退
    bridged = LLMClient(LLMClientConfig(
        model="MiniMax-M3", api_type="anthropic",
        base_url="https://api.minimaxi.com/anthropic", chat_protocol="auto",
    ))
    assert bridged._should_fallback_from_responses(_not_found()) is False


@pytest.mark.asyncio
async def test_auto_falls_back_to_chat_completions_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto 协议 native 404 → 记忆标记并回退 chat_completions 完成本次调用。"""
    client = LLMClient(LLMClientConfig(
        model="k3", api_type="openai",
        base_url="https://api.kimi.com/coding/v1", chat_protocol="auto",
    ))
    client._chat_via_responses = AsyncMock(side_effect=_not_found())  # type: ignore[method-assign]

    import litellm

    async def fake_acompletion(**kwargs: Any) -> Any:
        return litellm.ModelResponse(
            id="r1",
            choices=[{
                "index": 0,
                "message": {"role": "assistant", "content": "fallback-ok"},
                "finish_reason": "stop",
            }],
            model="k3",
        )

    monkeypatch.setattr("agent.llm.llm_client.litellm.acompletion", fake_acompletion)

    result = await client.chat([{"role": "user", "content": "hi"}])
    assert result.content == "fallback-ok"
    assert client._responses_native_blocked is True
    # 标记后后续调用不再尝试 responses
    client._chat_via_responses.assert_awaited_once()  # type: ignore[attr-defined]
    await client.chat([{"role": "user", "content": "hi again"}])
    client._chat_via_responses.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_explicit_responses_404_raises() -> None:
    """显式 responses 的 404 原样上抛（绝对官方接口语义）。"""
    client = LLMClient(LLMClientConfig(
        model="k3", api_type="openai",
        base_url="https://api.kimi.com/coding/v1", chat_protocol="responses",
    ))
    client._chat_via_responses = AsyncMock(side_effect=_not_found())  # type: ignore[method-assign]
    with pytest.raises(Exception, match="404"):
        await client.chat([{"role": "user", "content": "hi"}])
    assert client._responses_native_blocked is False


# ==================================================================
# Responses 路径端点报错自适应学习（与 chat_completions 同纪律）
# ==================================================================

def _responses_client() -> LLMClient:
    return LLMClient(LLMClientConfig(
        model="k3", api_type="openai",
        base_url="https://api.kimi.com/coding/v1", chat_protocol="responses",
    ))


def test_output_cap_error_parses_responses_field_name() -> None:
    """max_output_tokens 表述的上限报错同样可解析。"""
    import litellm

    exc = litellm.BadRequestError(
        message="Range of max_output_tokens should be [1, 32768]",
        model="k3", llm_provider="openai",
    )
    assert LLMClient._parse_output_cap_from_error(exc) == 32768
    exc2 = litellm.BadRequestError(
        message="does not support max output tokens > 65536",
        model="k3", llm_provider="openai",
    )
    assert LLMClient._parse_output_cap_from_error(exc2) == 65536


@pytest.mark.asyncio
async def test_responses_create_learns_output_cap() -> None:
    """非流式：max_output_tokens 超限 → 钳制重试并缓存学习结果。"""
    import litellm

    client = _responses_client()
    ok = ResponseResult(id="r1", status="completed", model="k3", output_text="ok")
    client.responses_create = AsyncMock(side_effect=[  # type: ignore[method-assign]
        litellm.BadRequestError(
            message="Range of max_output_tokens should be [1, 32768]",
            model="k3", llm_provider="openai",
        ),
        ok,
    ])
    result = await client.chat(
        [{"role": "user", "content": "hi"}], options={"max_tokens": 99999},
    )
    assert result.content == "ok"
    assert client._learned_output_cap == 32768
    second_kwargs: dict[str, Any] = client.responses_create.await_args_list[1].kwargs  # type: ignore[attr-defined]
    assert second_kwargs["max_output_tokens"] == 32768


@pytest.mark.asyncio
async def test_responses_stream_learns_tool_choice_rejection() -> None:
    """流式：强制 tool_choice 被拒 → 降级 auto 重试并缓存学习结果。"""
    import litellm

    client = _responses_client()
    client._learned_no_forced_tool_choice = False

    async def _fail_stream(**kwargs: Any) -> Any:
        raise litellm.BadRequestError(
            message="tool_choice is not supported by this endpoint",
            model="k3", llm_provider="openai",
        )
        yield  # pragma: no cover — 使其成为异步生成器

    async def _ok_stream(**kwargs: Any) -> Any:
        assert kwargs["tool_choice"] == "auto"
        yield normalize_stream_event({
            "type": "response.completed",
            "response": {"id": "r1", "status": "completed", "output": []},
        })

    calls = {"n": 0}

    def _stream_factory(**kwargs: Any) -> Any:
        calls["n"] += 1
        return _fail_stream(**kwargs) if calls["n"] == 1 else _ok_stream(**kwargs)

    client.responses_stream = _stream_factory  # type: ignore[method-assign]
    deltas = [
        d
        async for d in client.chat_stream(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}],
            tool_choice="required",
        )
    ]
    assert calls["n"] == 2
    assert client._learned_no_forced_tool_choice is True
    assert deltas[-1].finish_reason == "stop"


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


def test_messages_multimodal_parts_converted() -> None:
    """chat 格式 content block 应转换为 Responses 部件格式。"""
    _, payload = messages_to_responses_input([
        {"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "https://x/a.png"}},
        ]},
        {"role": "assistant", "content": [{"type": "text", "text": "是一只猫"}]},
    ])
    assert isinstance(payload, list)
    assert payload[0]["content"] == [
        {"type": "input_text", "text": "看图"},
        {"type": "input_image", "image_url": "https://x/a.png"},
    ]
    assert payload[1]["content"] == [{"type": "output_text", "text": "是一只猫"}]


def test_messages_single_assistant_not_folded() -> None:
    """单条 assistant 消息折叠为裸 string 会丢失 role（被按 user 处理），不应折叠。"""
    _, payload = messages_to_responses_input([
        {"role": "assistant", "content": "hi"},
    ])
    assert isinstance(payload, list)
    assert payload[0]["role"] == "assistant"


def test_messages_single_user_still_folded() -> None:
    _, payload = messages_to_responses_input([{"role": "user", "content": "hi"}])
    assert payload == "hi"


@pytest.mark.asyncio
async def test_chat_stream_via_responses() -> None:
    """chat_protocol=responses 时 chat_stream 应走 Responses 事件流。"""
    from agent.llm.responses.types import ResponseStreamEvent

    client = LLMClient(LLMClientConfig(
        model="gpt-4o",
        api_type="openai",
        base_url="https://api.openai.com/v1",
        chat_protocol="responses",
    ))

    async def fake_stream(**kwargs: Any) -> Any:
        yield ResponseStreamEvent(type="response.output_text.delta", data={"delta": "你"})
        yield ResponseStreamEvent(type="response.output_text.delta", data={"delta": "好"})
        yield ResponseStreamEvent(
            type="response.reasoning_summary_text.delta", data={"delta": "想了一下"},
        )
        yield ResponseStreamEvent(type="response.completed", data={
            "response": {
                "id": "resp_1",
                "status": "completed",
                "model": "gpt-4o",
                "output": [{
                    "type": "function_call",
                    "name": "lookup",
                    "call_id": "c1",
                    "arguments": "{}",
                }],
                "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            },
        })

    client.responses_stream = fake_stream  # type: ignore[method-assign]
    deltas = [d async for d in client.chat_stream([{"role": "user", "content": "hi"}])]
    assert [d.content for d in deltas[:2]] == ["你", "好"]
    assert deltas[2].reasoning_content == "想了一下"
    final = deltas[-1]
    assert final.finish_reason == "tool_calls"
    assert final.tool_calls and final.tool_calls[0].name == "lookup"
    assert final.usage is not None and final.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_chat_stream_via_responses_failed_event() -> None:
    """response.failed 终态事件应抛错而非静默结束。"""
    from agent.llm.responses.types import ResponseStreamEvent

    client = LLMClient(LLMClientConfig(
        model="gpt-4o",
        api_type="openai",
        base_url="https://api.openai.com/v1",
        chat_protocol="responses",
    ))

    async def fake_stream(**kwargs: Any) -> Any:
        yield ResponseStreamEvent(type="response.failed", data={
            "response": {"id": "r", "status": "failed", "error": {"message": "boom"}},
        })

    client.responses_stream = fake_stream  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="boom"):
        async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
            pass
