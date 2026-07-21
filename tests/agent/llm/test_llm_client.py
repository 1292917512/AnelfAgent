from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.llm.llm_client import (
    API_TYPES,
    LLMClient,
    LLMClientConfig,
    LLMNotConfiguredError,
    _PROXY_ENV_KEYS,
)


def _response(*, choices: list[Any], usage: Any = None) -> Any:
    return SimpleNamespace(
        choices=choices,
        usage=usage,
        model="test-model",
        model_dump=lambda: {"choices": []},
    )


def test_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="temperature"):
        LLMClientConfig(temperature=3)
    with pytest.raises(ValueError, match="api_type"):
        LLMClientConfig(api_type="unknown")
    with pytest.raises(ValueError, match="model_types"):
        LLMClientConfig(model_types=["invalid"])


def test_model_prefix_is_not_duplicated() -> None:
    config = LLMClientConfig(model="openai/gpt-4.1", api_type="openai")
    assert config.litellm_model == "openai/gpt-4.1"

    openrouter = LLMClientConfig(
        model="anthropic/claude-sonnet-4",
        api_type="openrouter",
    )
    assert openrouter.litellm_model == "openrouter/anthropic/claude-sonnet-4"


@pytest.mark.parametrize("api_type", API_TYPES)
def test_every_declared_provider_builds_a_model_identifier(api_type: str) -> None:
    config = LLMClientConfig(model="model", api_type=api_type)
    assert config.litellm_model.endswith("/model")


def test_build_kwargs_routes_provider_parameters() -> None:
    client = LLMClient(LLMClientConfig(
        model="o3",
        supports_reasoning=True,
        request_params={"api_version": "2025-01-01"},
        extra_body={"custom": True},
    ))

    kwargs = client._build_kwargs(
        [{"role": "user", "content": "hello"}],
        {"reasoning_effort": "high"},
    )

    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["api_version"] == "2025-01-01"
    assert kwargs["extra_body"] == {"custom": True, "reasoning_split": True}


def test_request_params_cannot_override_reserved_fields() -> None:
    with pytest.raises(ValueError, match="保留参数"):
        LLMClientConfig(
            model="gpt-4.1",
            request_params={"model": "other"},
        )


def test_anthropic_does_not_send_top_p() -> None:
    client = LLMClient(LLMClientConfig(
        model="claude-sonnet-4",
        api_type="anthropic",
    ))
    kwargs = client._build_kwargs(
        [{"role": "user", "content": "hello"}],
        {"top_p": 0.5},
    )
    assert "top_p" not in kwargs


def test_unsupported_reasoning_effort_is_not_silently_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent.llm.llm_client.litellm.supports_reasoning",
        lambda *_args, **_kwargs: False,
    )
    client = LLMClient(LLMClientConfig(
        model="qwen3",
        api_type="ollama",
        supports_reasoning=False,
    ))
    with pytest.raises(ValueError, match="reasoning_effort"):
        client._build_kwargs(
            [{"role": "user", "content": "hello"}],
            {"reasoning_effort": "high"},
        )


def test_adapt_messages_accepts_multimodal_system_content() -> None:
    client = LLMClient(LLMClientConfig(model="gpt-4.1"))
    adapted = client._adapt_messages([
        {"role": "system", "content": "rules"},
        {"role": "system", "content": [{"type": "text", "text": "more"}]},
        {"role": "user", "content": "hello"},
    ])
    assert adapted[0]["content"] == [
        {"type": "text", "text": "rules"},
        {"type": "text", "text": "more"},
    ]


def test_parse_empty_response_is_structured() -> None:
    client = LLMClient(LLMClientConfig(model="gpt-4.1"))
    result = client._parse_response(_response(choices=[]))
    assert result.content == ""
    assert result.finish_reason == "error"


def test_parse_tool_calls_skips_missing_function() -> None:
    malformed = SimpleNamespace(function=None, id="bad")
    assert LLMClient._parse_tool_calls([malformed]) == []


@pytest.mark.asyncio
async def test_unconfigured_client_fails_before_network() -> None:
    client = LLMClient(LLMClientConfig(model="", base_url=""))
    with pytest.raises(LLMNotConfiguredError):
        await client.chat([{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_embedding_uses_proxy_and_provider_params(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LLMClient(LLMClientConfig(
        model="text-embedding-3-small",
        proxy_url="http://proxy.local:8080",
        request_params={"dimensions": 256},
        extra_body={"custom": "value"},
    ))
    proxy = object()
    monkeypatch.setattr(client, "_get_proxy_client", lambda: proxy)
    call = AsyncMock(return_value=SimpleNamespace(
        data=[{"embedding": [0.1, 0.2]}],
    ))
    monkeypatch.setattr("agent.llm.llm_client.litellm.aembedding", call)

    result = await client.embed(["hello"])

    assert result == [[0.1, 0.2]]
    kwargs = call.await_args.kwargs
    assert kwargs["http_client"] is proxy
    assert kwargs["dimensions"] == 256
    assert kwargs["extra_body"] == {"custom": "value"}


@pytest.mark.asyncio
async def test_stream_reassembles_tools_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    function_a = SimpleNamespace(name="lookup", arguments='{"q":')
    function_b = SimpleNamespace(name=None, arguments='"x"}')
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content="",
                    reasoning_content=None,
                    reasoning_details=None,
                    tool_calls=[SimpleNamespace(index=0, id="call-1", function=function_a)],
                ),
                finish_reason=None,
            )],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content="",
                    reasoning_content=None,
                    reasoning_details=None,
                    tool_calls=[SimpleNamespace(index=0, id=None, function=function_b)],
                ),
                finish_reason="tool_calls",
            )],
            usage=None,
        ),
    ]

    class FakeStream:
        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self):
            async def iterator():
                for chunk in chunks:
                    yield chunk
            return iterator()

        async def aclose(self) -> None:
            self.closed = True

    stream = FakeStream()
    monkeypatch.setattr(
        "agent.llm.llm_client.litellm.acompletion",
        AsyncMock(return_value=stream),
    )
    client = LLMClient(LLMClientConfig(model="gpt-4.1"))

    deltas = [delta async for delta in client.chat_stream(
        [{"role": "user", "content": "hello"}],
    )]

    tool_calls = [tool for delta in deltas for tool in delta.tool_calls]
    assert len(tool_calls) == 1
    assert tool_calls[0].arguments == '{"q":"x"}'
    assert stream.closed is True


def test_normalize_tc_index_coerces_mixed_types() -> None:
    """provider 可能把 tool_call.index 返回为字符串，必须归一化为 int。"""
    assert LLMClient._normalize_tc_index(None, 0) == 0
    assert LLMClient._normalize_tc_index(0, 0) == 0
    assert LLMClient._normalize_tc_index("1", 0) == 1
    assert LLMClient._normalize_tc_index("abc", 3) == 3
    assert LLMClient._normalize_tc_index(2.0, 0) == 2


@pytest.mark.asyncio
async def test_stream_mixed_index_types_do_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """复现历史 bug：流式 chunk 中 tool_call.index 混合 int/str 时，
    sorted(tc_bufs) 抛 '<' not supported between instances of 'int' and 'str'。"""
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content="",
                    reasoning_content=None,
                    reasoning_details=None,
                    tool_calls=[
                        SimpleNamespace(index="0", id="call-1", function=SimpleNamespace(
                            name="lookup", arguments='{"q":')),
                        SimpleNamespace(index=1, id="call-2", function=SimpleNamespace(
                            name="send", arguments='{"x":')),
                    ],
                ),
                finish_reason=None,
            )],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content="",
                    reasoning_content=None,
                    reasoning_details=None,
                    tool_calls=[
                        SimpleNamespace(index=0, id=None, function=SimpleNamespace(
                            name=None, arguments='1}')),
                        SimpleNamespace(index="1", id=None, function=SimpleNamespace(
                            name=None, arguments='2}')),
                    ],
                ),
                finish_reason="tool_calls",
            )],
            usage=None,
        ),
    ]

    class FakeStream:
        def __aiter__(self):
            async def iterator():
                for chunk in chunks:
                    yield chunk
            return iterator()

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "agent.llm.llm_client.litellm.acompletion",
        AsyncMock(return_value=FakeStream()),
    )
    client = LLMClient(LLMClientConfig(model="gpt-4.1"))

    deltas = [delta async for delta in client.chat_stream(
        [{"role": "user", "content": "hello"}],
    )]

    tool_calls = [tool for delta in deltas for tool in delta.tool_calls]
    assert [t.name for t in tool_calls] == ["lookup", "send"]
    assert tool_calls[0].arguments == '{"q":1}'
    assert tool_calls[1].arguments == '{"x":2}'


@pytest.mark.asyncio
async def test_stream_consumer_cancel_closes_underlying_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(
                content="hello",
                reasoning_content=None,
                reasoning_details=None,
                tool_calls=None,
            ),
            finish_reason=None,
        )],
        usage=None,
    )

    class FakeStream:
        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self):
            async def iterator():
                yield chunk
                await asyncio.sleep(60)
            return iterator()

        async def aclose(self) -> None:
            self.closed = True

    stream = FakeStream()
    monkeypatch.setattr(
        "agent.llm.llm_client.litellm.acompletion",
        AsyncMock(return_value=stream),
    )
    client = LLMClient(LLMClientConfig(model="gpt-4.1"))
    generator = client.chat_stream([{"role": "user", "content": "hello"}])

    await anext(generator)
    await generator.aclose()

    assert stream.closed is True


@pytest.mark.asyncio
async def test_anthropic_proxy_environment_is_serialized_and_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    for key in _PROXY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    observed: list[str] = []
    response = _response(choices=[
        SimpleNamespace(
            message=SimpleNamespace(content="ok", tool_calls=None),
            finish_reason="stop",
        ),
    ])

    async def fake_completion(**_kwargs: Any) -> Any:
        observed.append(os.environ["HTTPS_PROXY"])
        await asyncio.sleep(0.01)
        assert os.environ["HTTPS_PROXY"] == observed[-1]
        return response

    monkeypatch.setattr(
        "agent.llm.llm_client.litellm.acompletion",
        fake_completion,
    )
    first = LLMClient(LLMClientConfig(
        model="claude",
        api_type="anthropic",
        proxy_url="http://proxy-a:8080",
    ))
    second = LLMClient(LLMClientConfig(
        model="claude",
        api_type="anthropic",
        proxy_url="http://proxy-b:8080",
    ))

    await asyncio.gather(
        first.chat([{"role": "user", "content": "a"}]),
        second.chat([{"role": "user", "content": "b"}]),
    )

    assert observed == ["http://proxy-a:8080", "http://proxy-b:8080"]
    assert all(key not in os.environ for key in _PROXY_ENV_KEYS)


@pytest.mark.asyncio
async def test_proxy_update_closes_stale_client() -> None:
    client = LLMClient(LLMClientConfig(
        model="gpt-4.1",
        proxy_url="http://old-proxy:8080",
    ))
    stale = SimpleNamespace(is_closed=False, aclose=AsyncMock())
    client._proxy_client = stale

    client.update_config(proxy_url="http://new-proxy:8080")
    await asyncio.sleep(0)

    stale.aclose.assert_awaited_once()
    assert client._proxy_client is None


def _tool() -> list[dict[str, Any]]:
    return [{
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "获取当前时间",
            "parameters": {"type": "object", "properties": {}},
        },
    }]


def test_forced_tool_choice_downgraded_when_unsupported() -> None:
    messages = [{"role": "user", "content": "hi"}]
    kimi = LLMClient(LLMClientConfig(
        model="k3[1m]", api_type="anthropic", supports_forced_tool_choice=False,
    ))
    kwargs = kimi._build_kwargs(messages, tools=_tool(), tool_choice="required")
    assert kwargs["tool_choice"] == "auto"
    # auto / none 与 thinking 兼容，原样保留
    for safe in ("auto", "none"):
        kwargs = kimi._build_kwargs(messages, tools=_tool(), tool_choice=safe)
        assert kwargs["tool_choice"] == safe
    # 默认开启：required 原样透传
    normal = LLMClient(LLMClientConfig(model="claude", api_type="anthropic"))
    kwargs = normal._build_kwargs(messages, tools=_tool(), tool_choice="required")
    assert kwargs["tool_choice"] == "required"


def test_tool_choice_downgrade_logged_once(monkeypatch) -> None:
    """强制降级产生 WARNING（每客户端仅首次），不再静默失效。"""
    import agent.llm.llm_client as client_mod

    warnings: list[str] = []
    monkeypatch.setattr(
        client_mod, "log",
        lambda msg, level="INFO", tag=None: warnings.append(msg) if level == "WARNING" else None,
    )
    kimi = LLMClient(LLMClientConfig(
        model="k3[1m]", api_type="anthropic", supports_forced_tool_choice=False,
    ))
    kimi._resolve_tool_choice("required")
    kimi._resolve_tool_choice("required")
    kimi._resolve_tool_choice({"type": "function", "function": {"name": "x"}})
    kimi._resolve_tool_choice("auto")  # 非强制值不触发
    assert len(warnings) == 1
    assert "降级" in warnings[0] and "k3[1m]" in warnings[0]


def test_supports_forced_tool_choice_serialization() -> None:
    config = LLMClientConfig(model="m", supports_forced_tool_choice=False)
    assert config.to_dict()["supports_forced_tool_choice"] is False
    assert config.to_model_dict()["supports_forced_tool_choice"] is False
    restored = LLMClientConfig.from_dict(config.to_dict())
    assert restored.supports_forced_tool_choice is False
    # 旧配置无此字段时默认 True
    legacy = {k: v for k, v in config.to_dict().items() if k != "supports_forced_tool_choice"}
    assert LLMClientConfig.from_dict(legacy).supports_forced_tool_choice is True
