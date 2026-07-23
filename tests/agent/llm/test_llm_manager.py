from __future__ import annotations

import json
import asyncio
from unittest.mock import AsyncMock

import pytest

from agent.llm.llm_client import LLMClient, LLMClientConfig, LLMNotConfiguredError
from agent.llm.llm_manager import LLMManager
from agent.llm.types import ChatResult


def _client(name: str) -> LLMClient:
    return LLMClient(LLMClientConfig(
        name=name,
        model=f"{name}-model",
        provider_id=name,
    ))


@pytest.mark.asyncio
async def test_empty_manager_fails_without_retrying_network(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    with pytest.raises(LLMNotConfiguredError):
        await manager.chat_with_fallback(
            [{"role": "user", "content": "hello"}],
            max_retries=3,
            timeout=1,
        )


@pytest.mark.asyncio
async def test_primary_retries_then_succeeds(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary")
    primary.chat = AsyncMock(side_effect=[
        RuntimeError("temporary"),
        ChatResult(content="ok"),
    ])
    monkeypatch.setattr(
        "agent.llm.llm_manager.asyncio.sleep",
        AsyncMock(),
    )

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hello"}],
        client=primary,
        max_retries=1,
        timeout=10,
    )

    assert result.content == "ok"
    assert primary.chat.await_count == 2


@pytest.mark.asyncio
async def test_timeout_cancels_underlying_chat(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary")
    cancelled = asyncio.Event()

    async def slow_chat(*_args, **_kwargs):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    primary.chat = slow_chat

    with pytest.raises(asyncio.TimeoutError):
        await manager.chat_with_fallback(
            [{"role": "user", "content": "hello"}],
            client=primary,
            max_retries=0,
            timeout=0.01,
        )

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_fallback_preserves_tools_and_options(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary")
    fallback = _client("fallback")
    primary.chat = AsyncMock(side_effect=RuntimeError("down"))
    fallback.chat = AsyncMock(return_value=ChatResult(content="fallback"))
    manager._clients = {"primary": primary, "fallback": fallback}
    manager._type_priorities = {"chat": ["primary", "fallback"]}

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hello"}],
        client=primary,
        options={"temperature": 0.2},
        tools=[{"type": "function", "function": {"name": "lookup"}}],
        tool_choice="auto",
        max_retries=0,
        timeout=10,
    )

    assert result.content == "fallback"
    fallback.chat.assert_awaited_once()
    kwargs = fallback.chat.await_args.kwargs
    assert kwargs["options"] == {"temperature": 0.2}
    assert kwargs["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_invalid_response_retries_or_falls_back(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    primary = _client("primary")
    fallback = _client("fallback")
    primary.chat = AsyncMock(return_value=ChatResult(
        content="",
        finish_reason="error",
    ))
    fallback.chat = AsyncMock(return_value=ChatResult(content="ok"))
    manager._clients = {"primary": primary, "fallback": fallback}
    manager._type_priorities = {"chat": ["primary", "fallback"]}

    result = await manager.chat_with_fallback(
        [{"role": "user", "content": "hello"}],
        client=primary,
        max_retries=0,
        timeout=10,
    )

    assert result.content == "ok"


def test_default_resolution_has_no_side_effect(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    first = _client("first")
    manager._clients = {"first": first}
    manager._type_priorities = {"chat": ["first"]}
    manager._default_chat = ""

    assert manager.get_default() is first
    assert manager.default_name == ""


def test_custom_model_uses_explicit_context_window(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    client = LLMClient(LLMClientConfig(
        name="custom",
        model="custom-model",
        max_tokens=4096,
        context_window=128000,
    ))
    manager._clients = {"custom": client}

    manager._register_unknown_models()

    import litellm
    assert litellm.model_cost[client.config.litellm_model]["max_input_tokens"] == 128000


@pytest.mark.asyncio
async def test_close_closes_all_clients(tmp_path) -> None:
    manager = LLMManager(str(tmp_path / "llm.json"))
    first = _client("first")
    second = _client("second")
    first.close = AsyncMock()
    second.close = AsyncMock()
    manager._clients = {"first": first, "second": second}

    await manager.close()

    first.close.assert_awaited_once()
    second.close.assert_awaited_once()


def test_config_round_trip_preserves_extended_parameters(tmp_path) -> None:
    config_path = tmp_path / "llm.json"
    config_path.write_text(json.dumps({
        "providers": [{
            "id": "provider",
            "base_url": "https://example.test/v1",
            "api_key": "secret",
            "api_type": "openai",
            "models": [{
                "id": "chat",
                "model": "model",
                "context_window": 128000,
                "request_params": {"api_version": "2025-01-01"},
                "extra_body": {"custom": True},
            }],
        }],
        "type_priorities": {"chat": ["chat"]},
        "default_chat": "chat",
    }), encoding="utf-8")

    manager = LLMManager(str(config_path))
    assert manager.save_config() is True
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    model = saved["providers"][0]["models"][0]
    assert model["context_window"] == 128000
    assert model["request_params"] == {"api_version": "2025-01-01"}
    assert model["extra_body"] == {"custom": True}


def test_error_redaction_removes_api_key(tmp_path) -> None:
    config_path = tmp_path / "llm.json"
    config_path.write_text(json.dumps({
        "providers": [{
            "id": "p1",
            "api_key": "top-secret-key",
            "models": [{"id": "default", "model": "model"}],
        }],
        "default_chat": "default",
    }), encoding="utf-8")
    manager = LLMManager(str(config_path))
    client = manager.get_client("default") or LLMClient(LLMClientConfig(
        model="model",
        api_key="top-secret-key",
    ))
    redacted = manager._safe_error(
        RuntimeError("request failed with top-secret-key"),
        client,
    )
    assert "top-secret-key" not in redacted
    assert "****" in redacted
