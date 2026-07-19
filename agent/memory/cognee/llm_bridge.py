"""LLMManager 模型配置到 Cognee LLM/Embedding 配置的解析桥接。

Cognee 的 LLM 适配层与主对话模型相互独立：结构化抽取走 cognee 自己的
provider 适配器（litellm + instructor），因此必须把 AnelfAgent 的模型配置
正确翻译为 cognee 的 set_llm_config / set_embedding_config payload。

关键约束：
- provider 与模型标识必须匹配（openai provider 不能配 anthropic/ 前缀模型硬跑），
  无法解析时抛 CogneeConfigError，由上层优雅降级而非放任无限重试。
- anthropic 协议模型统一走 cognee 的 custom provider（GenericAPIAdapter，
  底层 litellm）：与主对话调用路径一致、支持第三方兼容端点、默认 json_mode
  不产生 tool_choice；cognee 原生 AnthropicAdapter 依赖 anthropic SDK
  （本项目不安装）且不接受自定义端点，仅在显式选择时启用。
- thinking 模式端点不允许 tool_choice=required，需用 json_mode 规避。
"""

from __future__ import annotations

import importlib.util
from typing import Any, Optional

from core.log import log

from .config import (
    MODEL_SOURCE_CUSTOM,
    MODEL_SOURCE_MODEL,
    CogneeChatModelConfig,
    CogneeEmbeddingModelConfig,
)

# cognee 1.3 支持的 LLM provider（get_llm_client.LLMProvider）
SUPPORTED_CHAT_PROVIDERS = frozenset({
    "openai", "ollama", "anthropic", "custom",
    "gemini", "mistral", "azure", "bedrock",
})

# AnelfAgent api_type → cognee llm_provider
_API_TYPE_TO_COGNEE_PROVIDER = {
    "openai": "openai",
    # anthropic 协议统一经 litellm 路由（见模块 docstring），SDK 原生路径仅显式可选
    "anthropic": "custom",
    "ollama": "ollama",
    "gemini": "gemini",
    "azure": "azure",
    "mistral": "mistral",
    "bedrock": "bedrock",
}

# cognee embedding 引擎的显式分支；其余 provider 统一走 LiteLLMEmbeddingEngine
_COGNEE_NATIVE_EMBED_PROVIDERS = frozenset({"openai", "ollama", "azure"})

_ANTHROPIC_OFFICIAL_ENDPOINT = "https://api.anthropic.com"


class CogneeConfigError(RuntimeError):
    """Cognee 模型配置无法解析（配置不完整或与 cognee 不兼容）。"""


# ==================================================================
# Chat LLM 解析
# ==================================================================


def resolve_chat_llm_config(
    chat_cfg: CogneeChatModelConfig,
    manager: Any,
) -> dict[str, Any]:
    """解析 cognee set_llm_config payload；无法解析时抛 CogneeConfigError。"""
    if chat_cfg.source == MODEL_SOURCE_CUSTOM:
        return _resolve_custom_chat(chat_cfg)
    if chat_cfg.source == MODEL_SOURCE_MODEL:
        client = _find_client(manager, chat_cfg.model_id, "chat")
        if client is None:
            raise CogneeConfigError(
                f"指定的 chat 模型 '{chat_cfg.model_id}' 不存在，请在模型管理中确认"
            )
        return _payload_from_client(client, chat_cfg)
    return _resolve_auto_chat(chat_cfg, manager)


def _resolve_auto_chat(chat_cfg: CogneeChatModelConfig, manager: Any) -> dict[str, Any]:
    """自动映射：优先 openai 协议（兼容性最好），其次 cognee 支持的其他协议。"""
    default = manager.get_default()
    candidates: list[Any] = []
    if default is not None:
        candidates.append(default)
    for mid in getattr(manager, "_type_priorities", {}).get("chat", []):
        client = manager._clients.get(mid)
        if client is not None and client not in candidates:
            candidates.append(client)

    fallback: Optional[Any] = None
    for client in candidates:
        provider = _API_TYPE_TO_COGNEE_PROVIDER.get(client.config.api_type)
        if provider is None:
            continue
        if provider == "openai":
            return _payload_from_client(client, chat_cfg)
        if fallback is None:
            fallback = client

    if fallback is not None:
        log(
            f"Cognee: 无 openai 协议 chat 模型，回退到 "
            f"'{fallback.config.name}'（{fallback.config.api_type}）",
            "DEBUG",
        )
        return _payload_from_client(fallback, chat_cfg)

    raise CogneeConfigError(
        "没有可用的 cognee 兼容 chat 模型（支持 openai/anthropic/ollama/"
        "gemini/azure/mistral/bedrock 协议），请为 Cognee 单独指定模型或自定义配置"
    )


def _resolve_custom_chat(chat_cfg: CogneeChatModelConfig) -> dict[str, Any]:
    provider = chat_cfg.provider
    if provider not in SUPPORTED_CHAT_PROVIDERS:
        raise CogneeConfigError(
            f"cognee 不支持的 provider: '{provider}'，可选: {sorted(SUPPORTED_CHAT_PROVIDERS)}"
        )
    if provider == "anthropic" and importlib.util.find_spec("anthropic") is None:
        # 配置期快速失败：cognee 的 AnthropicAdapter 依赖原生 SDK，
        # 缺失时运行期会 import 失败并触发长时间重试风暴
        raise CogneeConfigError(
            "provider=anthropic 需要安装 anthropic 包（uv add anthropic）；"
            "第三方 Anthropic 兼容端点建议改用 provider=custom（经 litellm 路由，无需 SDK）"
        )
    if not chat_cfg.model:
        raise CogneeConfigError("自定义模型必须填写模型标识（如 openai/gpt-4o-mini）")
    payload: dict[str, Any] = {
        "llm_provider": provider,
        "llm_model": chat_cfg.model,
        "llm_api_key": chat_cfg.api_key or None,
        "llm_endpoint": chat_cfg.endpoint,
        "llm_temperature": 0.0,
    }
    if chat_cfg.api_version:
        payload["llm_api_version"] = chat_cfg.api_version
    _apply_chat_overrides(payload, chat_cfg)
    return payload


def _payload_from_client(client: Any, chat_cfg: CogneeChatModelConfig) -> dict[str, Any]:
    """把 LLMManager 客户端翻译为 cognee payload。"""
    cfg = client.config
    provider = _API_TYPE_TO_COGNEE_PROVIDER.get(cfg.api_type)
    if provider is None:
        raise CogneeConfigError(
            f"模型 '{cfg.name}' 的协议 {cfg.api_type} 不受 cognee 支持，"
            f"请改用自定义配置或选择其他模型"
        )
    payload: dict[str, Any] = {
        "llm_provider": provider,
        "llm_model": cfg.litellm_model,
        "llm_api_key": cfg.api_key or None,
        "llm_endpoint": cfg.base_url,
        "llm_temperature": 0.0,
    }
    _apply_chat_overrides(payload, chat_cfg, client=client)
    return payload


def _apply_chat_overrides(
    payload: dict[str, Any],
    chat_cfg: CogneeChatModelConfig,
    client: Optional[Any] = None,
) -> None:
    """应用 instructor_mode / max_tokens / extra_args 覆盖。

    端点标记为不支持强制 tool_choice（thinking 常开）且未显式指定模式时，
    自动回退 json_mode——instructor 的 tools 系模式会注入 tool_choice=required，
    在这类端点上必然 400。
    """
    mode = chat_cfg.instructor_mode
    if not mode and client is not None and not client.config.supports_forced_tool_choice:
        mode = "json_mode"
        log(
            f"Cognee: 模型 '{client.config.name}' 标记为不支持强制 tool_choice，"
            f"结构化输出自动使用 json_mode",
            "DEBUG",
        )
    if mode:
        payload["llm_instructor_mode"] = mode
    if chat_cfg.max_completion_tokens > 0:
        payload["llm_max_completion_tokens"] = chat_cfg.max_completion_tokens
    if chat_cfg.extra_args:
        payload["llm_args"] = dict(chat_cfg.extra_args)


def anthropic_env_bridge(payload: dict[str, Any]) -> None:
    """cognee 的 AnthropicAdapter 不接受 endpoint 参数，经环境变量桥接自定义端点。"""
    if payload.get("llm_provider") != "anthropic":
        return
    endpoint = str(payload.get("llm_endpoint") or "").strip()
    if not endpoint or endpoint.rstrip("/") == _ANTHROPIC_OFFICIAL_ENDPOINT:
        return
    import os
    os.environ["ANTHROPIC_BASE_URL"] = endpoint
    log(f"Cognee: Anthropic 自定义端点经 ANTHROPIC_BASE_URL 桥接: {endpoint}", "DEBUG")


# ==================================================================
# Embedding 解析
# ==================================================================


def resolve_embedding_llm_config(
    emb_cfg: CogneeEmbeddingModelConfig,
    manager: Any,
) -> Optional[dict[str, Any]]:
    """解析 cognee set_embedding_config payload；无可用 embedding 时返回 None。"""
    if emb_cfg.source == MODEL_SOURCE_CUSTOM:
        if not emb_cfg.model:
            raise CogneeConfigError("自定义 Embedding 必须填写模型标识")
        payload: dict[str, Any] = {
            "embedding_provider": emb_cfg.provider or "openai",
            "embedding_model": emb_cfg.model,
            "embedding_api_key": emb_cfg.api_key or None,
            "embedding_endpoint": emb_cfg.endpoint,
        }
        if emb_cfg.dimensions > 0:
            payload["embedding_dimensions"] = emb_cfg.dimensions
        return payload

    if emb_cfg.source == MODEL_SOURCE_MODEL:
        client = _find_client(manager, emb_cfg.model_id, "embedding")
        if client is None:
            raise CogneeConfigError(
                f"指定的 embedding 模型 '{emb_cfg.model_id}' 不存在，请在模型管理中确认"
            )
        return _embed_payload_from_client(client, emb_cfg.dimensions)

    client = manager.get_embedding_client()
    if client is None:
        return None
    return _embed_payload_from_client(client, emb_cfg.dimensions)


def _embed_payload_from_client(client: Any, dimensions: int = 0) -> dict[str, Any]:
    cfg = client.config
    provider = _embed_provider_name(cfg.api_type)
    if provider not in _COGNEE_NATIVE_EMBED_PROVIDERS:
        provider = "openai"
    payload: dict[str, Any] = {
        "embedding_provider": provider,
        "embedding_model": cfg.litellm_embed_model,
        "embedding_api_key": cfg.api_key or None,
        "embedding_endpoint": cfg.base_url,
    }
    dims = dimensions or getattr(client, "dimensions", 0) or 0
    if isinstance(dims, int) and dims > 0:
        payload["embedding_dimensions"] = dims
    return payload


def _embed_provider_name(api_type: str) -> str:
    aliases = {
        "openai_compatible": "openai",
        "azure_openai": "azure",
    }
    value = (api_type or "openai").strip().lower()
    return aliases.get(value, value)


# ==================================================================
# 内部辅助
# ==================================================================


def _find_client(manager: Any, model_id: str, model_type: str) -> Optional[Any]:
    """按 id 在 LLMManager 中查找指定类型的客户端。"""
    if not model_id:
        return None
    client = manager._clients.get(model_id)
    if client is None:
        return None
    if model_type and model_type not in (client.config.model_types or []):
        return None
    return client


def summarize_resolved(payload: Optional[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    """生成脱敏的已解析配置摘要，供状态接口展示。"""
    if not payload:
        return {}
    prefix = "llm" if kind == "chat" else "embedding"
    return {
        "provider": payload.get(f"{prefix}_provider", ""),
        "model": payload.get(f"{prefix}_model", ""),
        "endpoint": payload.get(f"{prefix}_endpoint", ""),
        "instructor_mode": payload.get("llm_instructor_mode", "") if kind == "chat" else "",
        "api_key_set": bool(payload.get(f"{prefix}_api_key")),
    }
