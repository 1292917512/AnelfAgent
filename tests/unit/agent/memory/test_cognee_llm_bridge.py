"""llm_bridge：LLMManager 模型配置到 Cognee payload 的解析测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent.memory.cognee.config import (
    CogneeChatModelConfig,
    CogneeEmbeddingModelConfig,
)
from agent.memory.cognee.llm_bridge import (
    CogneeConfigError,
    anthropic_env_bridge,
    resolve_chat_llm_config,
    resolve_embedding_llm_config,
    summarize_resolved,
)


class _FakeClientConfig:
    def __init__(
        self,
        name: str,
        api_type: str,
        model: str,
        *,
        model_types: list[str] | None = None,
        supports_forced_tool_choice: bool = True,
        supports_reasoning: bool = False,
        extra_body: dict | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.name = name
        self.enabled = True
        self.api_type = api_type
        self.model = model
        self.base_url = "https://example.com/v1"
        self.api_key = "sk-test"
        self.model_types = model_types or ["chat"]
        self.supports_forced_tool_choice = supports_forced_tool_choice
        self.supports_reasoning = supports_reasoning
        self.extra_body = extra_body or {}
        self.timeout = timeout

    @property
    def litellm_model(self) -> str:
        prefix = "ollama_chat" if self.api_type == "ollama" else self.api_type
        return f"{prefix}/{self.model}"

    @property
    def litellm_embed_model(self) -> str:
        return f"openai/{self.model}"


class _FakeClient:
    def __init__(self, config: _FakeClientConfig, dimensions: int = 0) -> None:
        self.config = config
        self.dimensions = dimensions


class _FakeManager:
    def __init__(self, clients: dict[str, _FakeClient], chat_order: list[str]) -> None:
        self._clients = clients
        self._type_priorities = {"chat": chat_order, "embedding": []}

    def get_default(self) -> Any:
        return self._clients[self._type_priorities["chat"][0]]

    def get_client(self, name: str) -> Any:
        return self._clients.get(name)

    def get_all_by_type(self, model_type: Any) -> list:
        value = getattr(model_type, "value", model_type)
        return [
            self._clients[mid] for mid in self._type_priorities.get(value, [])
            if mid in self._clients
        ]

    def get_embedding_client(self) -> Any:
        for client in self._clients.values():
            if "embedding" in client.config.model_types:
                return client
        return None


def _manager() -> _FakeManager:
    clients = {
        "qwen-thinking": _FakeClient(
            _FakeClientConfig(
                "qwen-thinking", "anthropic", "qwen3.8",
                supports_forced_tool_choice=False,
                supports_reasoning=True,
            )
        ),
        "sf-chat": _FakeClient(_FakeClientConfig("sf-chat", "openai", "Qwen3-32B")),
        "embed": _FakeClient(
            _FakeClientConfig("embed", "openai", "Qwen3-Embedding-8B", model_types=["embedding"]),
            dimensions=1024,
        ),
    }
    return _FakeManager(clients, ["qwen-thinking", "sf-chat"])


# ==================================================================
# Chat 解析
# ==================================================================


def test_auto_prefers_openai_client() -> None:
    payload = resolve_chat_llm_config(CogneeChatModelConfig().normalized(), _manager())
    assert payload["llm_provider"] == "openai"
    assert payload["llm_model"] == "openai/Qwen3-32B"
    assert payload["llm_endpoint"] == "https://example.com/v1"
    assert payload["llm_temperature"] == 0.0


@pytest.mark.parametrize("source", ["auto", "model", "custom"])
@pytest.mark.parametrize("override", [None, 180.0])
def test_extraction_request_inherits_model_timeout_unless_overridden(source, override):
    from agent.llm.config import DEFAULT_TIMEOUT

    manager = _manager()
    manager._clients["sf-chat"].config.timeout = 120.0
    payload = resolve_chat_llm_config(CogneeChatModelConfig(
        source=source, model_id="sf-chat", model="openai/test",
        extra_args={} if override is None else {"timeout": override},
    ), manager)
    expected = DEFAULT_TIMEOUT if source == "custom" else 120.0
    assert payload["llm_args"]["timeout"] == (override or expected)
    assert summarize_resolved(payload, kind="chat")["request_timeout_seconds"] == (override or expected)
    assert manager._clients["sf-chat"].config.timeout == 120.0


@pytest.mark.parametrize("value", [None, 0, -1, True, "120", float("inf"), float("nan")])
def test_extraction_rejects_unbounded_or_invalid_request_timeout(value):
    with pytest.raises(CogneeConfigError, match="timeout"):
        resolve_chat_llm_config(CogneeChatModelConfig(extra_args={"timeout": value}), _manager())


def test_auto_falls_back_to_supported_provider() -> None:
    """无 openai 协议模型时回退；anthropic 协议经 custom provider 走 litellm。"""
    manager = _manager()
    del manager._clients["sf-chat"]
    manager._type_priorities["chat"] = ["qwen-thinking"]
    payload = resolve_chat_llm_config(CogneeChatModelConfig().normalized(), manager)
    assert payload["llm_provider"] == "custom"
    assert payload["llm_model"] == "anthropic/qwen3.8"
    assert payload["llm_endpoint"] == "https://example.com/v1"


def test_auto_raises_when_no_compatible_client() -> None:
    manager = _FakeManager({}, [])
    with pytest.raises(CogneeConfigError, match="没有可用的 cognee 兼容"):
        resolve_chat_llm_config(CogneeChatModelConfig().normalized(), manager)


def test_auto_skips_disabled_chat_models() -> None:
    manager = _manager()
    manager._clients["sf-chat"].config.enabled = False
    payload = resolve_chat_llm_config(CogneeChatModelConfig(), manager)
    assert payload["llm_provider"] == "custom"
    manager._clients["qwen-thinking"].config.enabled = False
    with pytest.raises(CogneeConfigError, match="没有可用的 cognee 兼容"):
        resolve_chat_llm_config(CogneeChatModelConfig(), manager)


def test_model_source_pins_client_and_uses_native_structured_output() -> None:
    """未显式指定 Instructor 时使用原生结构化输出，不强制工具调用。"""
    cfg = CogneeChatModelConfig(source="model", model_id="qwen-thinking").normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert payload["llm_provider"] == "custom"
    assert payload["structured_output_framework"] == "litellm_native"


def test_model_source_explicit_instructor_mode_wins() -> None:
    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", instructor_mode="tools",
    ).normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert payload["llm_instructor_mode"] == "tool_call"


def test_model_source_missing_model_raises() -> None:
    cfg = CogneeChatModelConfig(source="model", model_id="nope").normalized()
    with pytest.raises(CogneeConfigError, match="不存在"):
        resolve_chat_llm_config(cfg, _manager())


def test_custom_source_validates_provider_and_model() -> None:
    cfg = CogneeChatModelConfig(source="custom", provider="unknown", model="x").normalized()
    with pytest.raises(CogneeConfigError, match="不支持的 provider"):
        resolve_chat_llm_config(cfg, _manager())

    cfg = CogneeChatModelConfig(source="custom", provider="openai", model="").normalized()
    with pytest.raises(CogneeConfigError, match="模型标识"):
        resolve_chat_llm_config(cfg, _manager())


def test_custom_source_payload() -> None:
    cfg = CogneeChatModelConfig(
        source="custom",
        provider="custom",
        model="openai/gpt-4o-mini",
        endpoint="http://localhost:8000/v1",
        api_key="sk-c",
        instructor_mode="json_mode",
        max_completion_tokens=4096,
    ).normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert payload["llm_provider"] == "custom"
    assert payload["llm_model"] == "openai/gpt-4o-mini"
    assert payload["llm_instructor_mode"] == "json_mode"
    assert payload["llm_max_completion_tokens"] == 4096
    assert payload["llm_args"]["max_tokens"] == 4096
    assert payload["structured_output_framework"] == "instructor"


def test_custom_anthropic_native_does_not_require_sdk() -> None:
    """Anthropic 原生结构化输出经 LiteLLM 路由。"""
    cfg = CogneeChatModelConfig(
        source="custom", provider="anthropic", model="claude-sonnet-4-5",
    ).normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert payload["structured_output_framework"] == "litellm_native"


# ==================================================================
# thinking 预算注入与 extra_body 透传
# ==================================================================


def test_reasoning_model_without_contract_does_not_invent_thinking_parameters() -> None:
    cfg = CogneeChatModelConfig(source="model", model_id="qwen-thinking").normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert "extra_body" not in payload["llm_args"]
    assert "reasoning_effort" not in payload["llm_args"]


def test_non_reasoning_model_no_thinking_injection() -> None:
    cfg = CogneeChatModelConfig(source="model", model_id="sf-chat").normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert "extra_body" not in payload.get("llm_args", {})
    # 结构化抽取始终显式设置输出预算，防止端点小默认值截断
    assert payload["llm_max_completion_tokens"] >= 16384
    assert payload["llm_args"]["max_tokens"] == payload["llm_max_completion_tokens"]


def test_user_extra_body_thinking_wins() -> None:
    """模型 extra_body 中显式配置的 thinking 不被自动注入覆盖。"""
    manager = _manager()
    manager._clients["qwen-thinking"].config.extra_body = {
        "thinking": {"type": "enabled", "budget_tokens": 4096},
    }
    cfg = CogneeChatModelConfig(source="model", model_id="qwen-thinking").normalized()
    payload = resolve_chat_llm_config(cfg, manager)
    thinking = payload["llm_args"]["extra_body"]["thinking"]
    assert thinking["budget_tokens"] == 4096


def test_extra_body_reserved_keys_filtered() -> None:
    manager = _manager()
    manager._clients["sf-chat"].config.extra_body = {
        "temperature": 0.9,
        "tool_choice": "required",
        "custom_flag": True,
    }
    cfg = CogneeChatModelConfig(source="model", model_id="sf-chat").normalized()
    payload = resolve_chat_llm_config(cfg, manager)
    extra_body = payload["llm_args"]["extra_body"]
    assert "temperature" not in extra_body
    assert "tool_choice" not in extra_body
    assert extra_body["custom_flag"] is True


# ==================================================================
# 思考等级（reasoning_effort）显式配置
# ==================================================================


def test_reasoning_effort_off_uses_litellm_contract() -> None:
    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", reasoning_effort="off",
    ).normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert payload["llm_args"]["reasoning_effort"] == "none"
    assert "extra_body" not in payload["llm_args"]


@pytest.mark.parametrize("effort", ["low", "medium", "high", "max"])
def test_reasoning_effort_levels_use_litellm_parameter(effort: str) -> None:
    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", reasoning_effort=effort,
    ).normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert payload["llm_args"]["reasoning_effort"] == effort
    assert "extra_body" not in payload["llm_args"]


def test_reasoning_effort_overrides_model_extra_body() -> None:
    manager = _manager()
    manager._clients["qwen-thinking"].config.extra_body = {
        "reasoning_effort": "high",
    }
    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", reasoning_effort="low",
    ).normalized()
    payload = resolve_chat_llm_config(cfg, manager)
    assert payload["llm_args"]["reasoning_effort"] == "low"


def test_reasoning_effort_invalid_falls_back_to_auto() -> None:
    manager = _manager()
    manager._clients["qwen-thinking"].config.reasoning_effort = "high"
    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", reasoning_effort="extreme",
    ).normalized()
    assert cfg.reasoning_effort == ""
    payload = resolve_chat_llm_config(cfg, manager)
    assert payload["llm_args"]["reasoning_effort"] == "high"


def test_explicit_extra_args_override() -> None:
    manager = _manager()
    cfg = CogneeChatModelConfig(
        source="model",
        model_id="qwen-thinking",
        extra_args={"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 8192}}},
    ).normalized()
    payload = resolve_chat_llm_config(cfg, manager)
    thinking = payload["llm_args"]["extra_body"]["thinking"]
    assert thinking["budget_tokens"] == 8192


# ==================================================================
# Anthropic 端点桥接
# ==================================================================


def test_anthropic_env_bridge_sets_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    anthropic_env_bridge({
        "llm_provider": "anthropic",
        "llm_endpoint": "https://api.minimaxi.com/anthropic",
    })
    import os
    assert os.environ["ANTHROPIC_BASE_URL"] == "https://api.minimaxi.com/anthropic"


def test_anthropic_env_bridge_ignores_official_and_other_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    anthropic_env_bridge({
        "llm_provider": "anthropic",
        "llm_endpoint": "https://api.anthropic.com",
    })
    anthropic_env_bridge({
        "llm_provider": "openai",
        "llm_endpoint": "https://example.com/v1",
    })
    import os
    assert "ANTHROPIC_BASE_URL" not in os.environ


# ==================================================================
# Embedding 解析
# ==================================================================


def test_embedding_auto_uses_manager_client() -> None:
    payload = resolve_embedding_llm_config(
        CogneeEmbeddingModelConfig().normalized(), _manager(),
    )
    assert payload is not None
    assert payload["embedding_provider"] == "openai"
    assert payload["embedding_model"] == "openai/Qwen3-Embedding-8B"
    assert payload["embedding_dimensions"] == 1024
    assert payload["embedding_max_completion_tokens"] == 8191


def test_embedding_custom_includes_input_cap() -> None:
    cfg = CogneeEmbeddingModelConfig(
        source="custom", model="text-embedding-v4", provider="openai",
    ).normalized()
    payload = resolve_embedding_llm_config(cfg, _manager())
    assert payload is not None
    assert payload["embedding_max_completion_tokens"] == 8191


def test_embedding_custom_requires_model() -> None:
    cfg = CogneeEmbeddingModelConfig(source="custom", model="").normalized()
    with pytest.raises(CogneeConfigError, match="模型标识"):
        resolve_embedding_llm_config(cfg, _manager())


def test_embedding_model_source_missing_raises() -> None:
    cfg = CogneeEmbeddingModelConfig(source="model", model_id="nope").normalized()
    with pytest.raises(CogneeConfigError, match="不存在"):
        resolve_embedding_llm_config(cfg, _manager())


# ==================================================================
# 摘要脱敏
# ==================================================================


def test_summarize_resolved_masks_key() -> None:
    summary = summarize_resolved(
        {
            "llm_provider": "openai",
            "llm_model": "openai/gpt-4o-mini",
            "llm_endpoint": "https://api.openai.com/v1",
            "llm_api_key": "sk-secret",
            "llm_instructor_mode": "json_mode",
        },
        kind="chat",
    )
    assert summary["api_key_set"] is True
    assert "sk-secret" not in str(summary)
    assert summary["instructor_mode"] == "json_mode"


@pytest.mark.parametrize("source", ["auto", "model", "custom"])
def test_all_sources_forward_explicit_budget_and_preserve_config(source):
    cfg = CogneeChatModelConfig(source=source, model_id="sf-chat", model="openai/test",
        max_completion_tokens=4096, extra_args={"max_tokens": 9999})
    payload = resolve_chat_llm_config(cfg, _manager())
    assert payload["llm_max_completion_tokens"] == payload["llm_args"]["max_tokens"] == 4096
    assert cfg.extra_args == {"max_tokens": 9999}


def test_custom_reasoning_uses_litellm_parameter_and_model_uses_declared_contract():
    custom = resolve_chat_llm_config(CogneeChatModelConfig(
        source="custom", model="openai/test", reasoning_effort="off"), _manager())
    assert custom["llm_args"]["reasoning_effort"] == "none"
    manager = _manager()
    manager._clients["sf-chat"].config.thinking = {
        "param": "reasoning_effort", "map": {"low": "low"}, "off": "none"}
    model = resolve_chat_llm_config(CogneeChatModelConfig(
        source="model", model_id="sf-chat", reasoning_effort="low"), manager)
    assert model["llm_args"]["reasoning_effort"] == "low"


def test_thinking_budget_cannot_consume_entire_completion_budget():
    with pytest.raises(CogneeConfigError, match="思考预算"):
        resolve_chat_llm_config(CogneeChatModelConfig(source="model", model_id="qwen-thinking",
            extra_args={"extra_body": {"thinking": {"type": "enabled", "budget_tokens": 4096}}},
            max_completion_tokens=4096), _manager())


@pytest.mark.parametrize("source", ["auto", "model"])
@pytest.mark.parametrize("effort", ["off", "low", "high"])
def test_model_thinking_contract_applies_to_all_sources(source: str, effort: str) -> None:
    manager = _manager()
    model = manager._clients["sf-chat"].config
    model.thinking = {"param": "thinking.type", "on": "enabled", "off": "disabled"}
    model.extra_body = {"thinking": {"type": "enabled", "budget_tokens": 4096}}
    payload = resolve_chat_llm_config(CogneeChatModelConfig(
        source=source, model_id="sf-chat", reasoning_effort=effort,
    ), manager)
    thinking = payload["llm_args"]["extra_body"]["thinking"]
    assert thinking["type"] == ("disabled" if effort == "off" else "enabled")
    assert ("budget_tokens" in thinking) is (effort != "off")
    assert model.extra_body["thinking"]["budget_tokens"] == 4096


def test_unsupported_explicit_effort_is_not_silently_ignored() -> None:
    manager = _manager()
    manager._clients["sf-chat"].config.thinking = {"param": "reasoning_effort", "map": {"low": "low"}}
    with pytest.raises(CogneeConfigError, match="不支持 'high'"):
        resolve_chat_llm_config(CogneeChatModelConfig(
            source="model", model_id="sf-chat", reasoning_effort="high",
        ), manager)


@pytest.mark.parametrize("effort", ["off", "high"])
async def test_cognee_native_adapter_receives_reasoning_and_output_budget(
    monkeypatch: pytest.MonkeyPatch, effort: str,
) -> None:
    import importlib

    import litellm
    from cognee.infrastructure.llm.config import LLMConfig
    from pydantic import BaseModel

    class Extracted(BaseModel):
        fact: str

    factory = importlib.import_module(
        "cognee.infrastructure.llm.structured_output_framework.litellm_native.get_native_client",
    )
    payload = resolve_chat_llm_config(CogneeChatModelConfig(
        source="custom", model="openai/gpt-4o-mini", api_key="test-placeholder",
        endpoint="https://example.invalid/v1", reasoning_effort=effort, max_completion_tokens=8192,
        extra_args={"timeout": 180.0},
    ), _manager())
    monkeypatch.setattr(factory, "get_llm_context_config", lambda: LLMConfig(**payload))
    completion = AsyncMock(return_value=litellm.ModelResponse(
        choices=[{"message": {"role": "assistant", "content": '{"fact":"verified"}'}, "finish_reason": "stop"}],
    ))
    monkeypatch.setattr(litellm, "acompletion", completion)
    result = await factory.get_native_client().acreate_structured_output("source", "extract", Extracted)
    assert result.fact == "verified"
    completion.assert_awaited_once()
    request = completion.call_args.kwargs
    assert request["reasoning_effort"] == ("none" if effort == "off" else effort)
    assert request["max_tokens"] == 8192
    assert request["api_base"] == "https://example.invalid/v1"
    assert request["timeout"] == 180.0


@pytest.mark.parametrize("retry_first_request", [False, True])
async def test_cognee_generation_respects_model_budget_across_slow_requests_and_retries(
    monkeypatch, retry_first_request,
):
    import asyncio
    import importlib

    import litellm
    from cognee.infrastructure.llm.config import LLMConfig
    from cognee.infrastructure.llm.structured_output_framework.litellm_native.native_adapter import NativeLiteLLMAdapter
    from tenacity import wait_none

    from agent.memory.cognee import adapters

    adapters.install()
    gateway = importlib.import_module("cognee.infrastructure.llm.LLMGateway")
    factory = importlib.import_module(
        "cognee.infrastructure.llm.structured_output_framework.litellm_native.get_native_client",
    )
    payload = resolve_chat_llm_config(CogneeChatModelConfig(
        source="custom", model="openai/gpt-4o-mini", api_key="test-placeholder",
        endpoint="https://example.invalid/v1", extra_args={"timeout": 180.0},
    ), _manager())
    monkeypatch.setattr(gateway, "get_llm_config", lambda: LLMConfig(**payload))
    monkeypatch.setattr(factory, "get_llm_context_config", lambda: LLMConfig(**payload))
    monkeypatch.setattr(NativeLiteLLMAdapter.acreate_structured_output.retry, "wait", wait_none())
    loop = asyncio.get_running_loop()
    clock = loop.time
    elapsed = 0.0
    calls = 0

    async def completion(**kwargs):
        nonlocal elapsed, calls
        calls += 1
        assert kwargs["timeout"] == 180.0
        elapsed = 65.0 if retry_first_request else 95.0
        for _ in range(3):
            await asyncio.sleep(0)
        if retry_first_request and calls == 1:
            raise TimeoutError("transient provider timeout")
        return litellm.ModelResponse(choices=[{
            "message": {"role": "assistant", "content": "extracted"}, "finish_reason": "stop",
        }])

    monkeypatch.setattr(litellm, "acompletion", completion)
    with monkeypatch.context() as virtual_clock:
        virtual_clock.setattr(loop, "time", lambda: clock() + elapsed)
        result = await gateway.LLMGateway.acreate_structured_output("source", "extract", str)
    assert result == "extracted"
    assert calls == (2 if retry_first_request else 1)
