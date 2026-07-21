"""llm_bridge：LLMManager 模型配置到 Cognee payload 的解析测试。"""

from __future__ import annotations

from typing import Any

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
    ) -> None:
        self.name = name
        self.api_type = api_type
        self.model = model
        self.base_url = "https://example.com/v1"
        self.api_key = "sk-test"
        self.model_types = model_types or ["chat"]
        self.supports_forced_tool_choice = supports_forced_tool_choice
        self.supports_reasoning = supports_reasoning
        self.extra_body = extra_body or {}

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
    manager.get_default = lambda: None  # type: ignore[method-assign]
    with pytest.raises(CogneeConfigError, match="没有可用的 cognee 兼容"):
        resolve_chat_llm_config(CogneeChatModelConfig().normalized(), manager)


def test_model_source_pins_client_and_json_mode_fallback() -> None:
    """thinking 端点（不支持强制 tool_choice）自动回退 json_mode。"""
    cfg = CogneeChatModelConfig(source="model", model_id="qwen-thinking").normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert payload["llm_provider"] == "custom"
    assert payload["llm_instructor_mode"] == "json_mode"


def test_model_source_explicit_instructor_mode_wins() -> None:
    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", instructor_mode="tools",
    ).normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    assert payload["llm_instructor_mode"] == "tools"


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


def test_custom_anthropic_requires_sdk() -> None:
    """显式选择 anthropic provider 但未安装 SDK 时，配置期快速失败。"""
    cfg = CogneeChatModelConfig(
        source="custom", provider="anthropic", model="claude-sonnet-4-5",
    ).normalized()
    with pytest.raises(CogneeConfigError, match="anthropic"):
        resolve_chat_llm_config(cfg, _manager())


# ==================================================================
# thinking 预算注入与 extra_body 透传
# ==================================================================


def test_reasoning_model_gets_thinking_budget() -> None:
    """thinking 模型自动注入思考预算，防止推理 token 吃光输出上限。"""
    cfg = CogneeChatModelConfig(source="model", model_id="qwen-thinking").normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    thinking = payload["llm_args"]["extra_body"]["thinking"]
    assert thinking == {"type": "enabled", "budget_tokens": 2048}


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


def test_reasoning_effort_off_disables_thinking() -> None:
    """reasoning_effort=off 强制注入 thinking disabled。"""
    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", reasoning_effort="off",
    ).normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    thinking = payload["llm_args"]["extra_body"]["thinking"]
    assert thinking == {"type": "disabled"}


def test_reasoning_effort_levels_budget() -> None:
    """low/medium/high 映射到对应思考预算；max 不限制预算。"""
    cases = {"low": 1024, "medium": 2048, "high": 4096}
    for effort, expected in cases.items():
        cfg = CogneeChatModelConfig(
            source="model", model_id="qwen-thinking", reasoning_effort=effort,
        ).normalized()
        payload = resolve_chat_llm_config(cfg, _manager())
        thinking = payload["llm_args"]["extra_body"]["thinking"]
        assert thinking == {"type": "enabled", "budget_tokens": expected}, effort

    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", reasoning_effort="max",
    ).normalized()
    payload = resolve_chat_llm_config(cfg, _manager())
    thinking = payload["llm_args"]["extra_body"]["thinking"]
    assert thinking == {"type": "enabled"}


def test_reasoning_effort_overrides_model_extra_body() -> None:
    """显式 reasoning_effort 优先级高于模型 extra_body.thinking。"""
    manager = _manager()
    manager._clients["qwen-thinking"].config.extra_body = {
        "thinking": {"type": "enabled", "budget_tokens": 4096},
    }
    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", reasoning_effort="low",
    ).normalized()
    payload = resolve_chat_llm_config(cfg, manager)
    thinking = payload["llm_args"]["extra_body"]["thinking"]
    assert thinking["budget_tokens"] == 1024


def test_reasoning_effort_invalid_falls_back_to_auto() -> None:
    """非法 reasoning_effort 归一化为 auto，保持按 supports_reasoning 自动注入。"""
    cfg = CogneeChatModelConfig(
        source="model", model_id="qwen-thinking", reasoning_effort="extreme",
    ).normalized()
    assert cfg.reasoning_effort == ""
    payload = resolve_chat_llm_config(cfg, _manager())
    thinking = payload["llm_args"]["extra_body"]["thinking"]
    assert thinking == {"type": "enabled", "budget_tokens": 2048}


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
