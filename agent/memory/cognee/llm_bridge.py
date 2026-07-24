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

# 模型 extra_body 透传时的保留键（与 cognee/instructor 自身参数冲突，丢弃）
_LLM_ARGS_RESERVED_KEYS = frozenset({
    "model", "messages", "max_tokens", "temperature",
    "tools", "tool_choice", "response_format", "stream",
})

# thinking 模型的思考预算上限（tokens）。部分端点强制开启 thinking 且不可关闭，
# 推理 token 与正文共享 max_tokens 预算，不加限制会把输出截断
_THINKING_BUDGET_TOKENS = 2048

# 思考等级 → thinking budget_tokens 映射；与主 LLM 系统 reasoning_effort 档位对齐，
# max 不封顶交由端点决定（None 表示不传 budget_tokens，仅启用思考）
_EFFORT_TO_BUDGET: dict[str, Optional[int]] = {
    "minimal": 512,
    "low": 1024,
    "medium": _THINKING_BUDGET_TOKENS,
    "high": 4096,
    "xhigh": 8192,
    "max": None,
}

# cognee 结构化抽取的输出预算下限：正文需容纳 nodes+edges JSON，
# thinking 模型的推理 token 与其共享该预算，过小必然截断
_MIN_EXTRACTION_MAX_TOKENS = 16384


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
    if chat_cfg.extra_args:
        payload["llm_args"] = dict(chat_cfg.extra_args)
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
    llm_args = _build_llm_args(cfg, chat_cfg)
    if llm_args:
        payload["llm_args"] = llm_args
    _apply_chat_overrides(payload, chat_cfg, client=client)
    _ensure_extraction_budget(payload, client)
    return payload


def _build_llm_args(cfg: Any, chat_cfg: CogneeChatModelConfig) -> dict[str, Any]:
    """组装透传给 litellm 的 llm_args。

    - 模型自身 extra_body 过滤保留键后透传，与主对话行为对齐；
    - thinking 注入优先级：chat.reasoning_effort 显式档位 > 模型 extra_body.thinking
      > 按 supports_reasoning 自动注入 medium 预算；reasoning_effort=off 时强制
      关闭思考（部分端点强制常开，会忽略 disabled，属正常）；
    - chat 配置中的 extra_args 显式指定，优先级最高。
    """
    extra_body = {
        key: value for key, value in (getattr(cfg, "extra_body", None) or {}).items()
        if key not in _LLM_ARGS_RESERVED_KEYS
    }
    effort = chat_cfg.reasoning_effort
    if effort == "off":
        extra_body["thinking"] = {"type": "disabled"}
        log(f"Cognee: 模型 '{cfg.name}' 思考模式已显式关闭", "DEBUG")
    elif effort in _EFFORT_TO_BUDGET:
        budget = _EFFORT_TO_BUDGET[effort]
        thinking: dict[str, Any] = {"type": "enabled"}
        if budget is not None:
            thinking["budget_tokens"] = budget
        extra_body["thinking"] = thinking
        log(
            f"Cognee: 模型 '{cfg.name}' 思考等级 {effort}"
            + (f"（预算 {budget} tokens）" if budget else "（不限制预算）"),
            "DEBUG",
        )
    elif getattr(cfg, "supports_reasoning", False) and "thinking" not in extra_body:
        max_tokens = chat_cfg.max_completion_tokens or 16384
        budget = max(1024, min(_THINKING_BUDGET_TOKENS, max_tokens - 1024))
        extra_body["thinking"] = {"type": "enabled", "budget_tokens": budget}
        log(
            f"Cognee: 模型 '{cfg.name}' 为 thinking 模型，"
            f"思考预算限制为 {budget} tokens（可在模型 extra_body 中覆盖）",
            "DEBUG",
        )
    llm_args: dict[str, Any] = {}
    if extra_body:
        llm_args["extra_body"] = extra_body
    llm_args.update(chat_cfg.extra_args)
    return llm_args


def _apply_chat_overrides(
    payload: dict[str, Any],
    chat_cfg: CogneeChatModelConfig,
    client: Optional[Any] = None,
) -> None:
    """应用 instructor_mode / max_tokens 覆盖。

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


def _ensure_extraction_budget(
    payload: dict[str, Any],
    client: Optional[Any],
) -> None:
    """为结构化抽取保证足够的输出预算。

    cognee 默认 llm_max_completion_tokens=16384，但部分适配器不回填默认值，
    缺省时端点会按自身小默认值截断；thinking 模型的推理 token 还与正文
    共享该预算，过小会在长记忆抽取时反复 max_tokens 截断。模型自身
    max_tokens 配置（对话用途）若偏小，自动抬升到下限，不影响主对话配置。
    """
    if "llm_max_completion_tokens" in payload:
        return
    model_max = int(getattr(client.config, "max_tokens", 0) or 0) if client is not None else 0
    budget = max(model_max, _MIN_EXTRACTION_MAX_TOKENS)
    payload["llm_max_completion_tokens"] = budget
    llm_args = payload.setdefault("llm_args", {})
    llm_args.setdefault("max_tokens", budget)
    if model_max and model_max < _MIN_EXTRACTION_MAX_TOKENS and client is not None:
        log(
            f"Cognee: 模型 '{client.config.name}' max_tokens={model_max} 偏小，"
            f"结构化抽取输出预算抬升至 {budget}（不影响主对话配置）",
            "DEBUG",
        )


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
