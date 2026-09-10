"""LLM 协议能力矩阵：Chat Completions / Responses。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet


class ChatProtocol(str, Enum):
    """模型对话协议。"""

    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    AUTO = "auto"


class TransportMode(str, Enum):
    """Responses 实际传输模式。"""

    NATIVE = "native"
    BRIDGE = "bridge"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    """单个 api_type 的 Responses 能力声明。"""

    create: TransportMode
    stream: TransportMode
    retrieve: TransportMode
    delete: TransportMode
    cancel: TransportMode
    compact: TransportMode
    previous_response_id: TransportMode
    builtin_tools: TransportMode


_NATIVE = ProviderCapability(
    create=TransportMode.NATIVE,
    stream=TransportMode.NATIVE,
    retrieve=TransportMode.NATIVE,
    delete=TransportMode.NATIVE,
    cancel=TransportMode.NATIVE,
    compact=TransportMode.NATIVE,
    previous_response_id=TransportMode.NATIVE,
    builtin_tools=TransportMode.NATIVE,
)

_BRIDGE = ProviderCapability(
    create=TransportMode.BRIDGE,
    stream=TransportMode.BRIDGE,
    retrieve=TransportMode.UNSUPPORTED,
    delete=TransportMode.UNSUPPORTED,
    cancel=TransportMode.UNSUPPORTED,
    compact=TransportMode.UNSUPPORTED,
    previous_response_id=TransportMode.UNSUPPORTED,
    builtin_tools=TransportMode.UNSUPPORTED,
)

_UNSUPPORTED = ProviderCapability(
    create=TransportMode.UNSUPPORTED,
    stream=TransportMode.UNSUPPORTED,
    retrieve=TransportMode.UNSUPPORTED,
    delete=TransportMode.UNSUPPORTED,
    cancel=TransportMode.UNSUPPORTED,
    compact=TransportMode.UNSUPPORTED,
    previous_response_id=TransportMode.UNSUPPORTED,
    builtin_tools=TransportMode.UNSUPPORTED,
)

CHAT_PROTOCOLS: FrozenSet[str] = frozenset(item.value for item in ChatProtocol)

_NATIVE_API_TYPES: FrozenSet[str] = frozenset({"openai", "azure"})

_BRIDGE_API_TYPES: FrozenSet[str] = frozenset({
    "anthropic",
    "gemini",
    "bedrock",
    "vertex_ai",
    "deepseek",
    "groq",
    "mistral",
    "openrouter",
    "together_ai",
    "fireworks_ai",
    "xai",
    "volcengine",
    "dashscope",
    "ollama",
    "cohere",
    "huggingface",
    "cloudflare",
    "perplexity",
    "cerebras",
    "sambanova",
})

BUILTIN_TOOL_TYPES: FrozenSet[str] = frozenset({
    "web_search",
    "web_search_preview",
    "file_search",
    "code_interpreter",
    "computer_use",
    "computer_use_preview",
    "image_generation",
    "mcp",
    "local_shell",
    "shell",
    "apply_patch",
    # 百炼系扩展内置工具（Responses tools 声明原样透传，由端点校验）
    "web_extractor",
    "t2i_search",
    "i2i_search",
})


def get_provider_capability(api_type: str) -> ProviderCapability:
    """返回指定 api_type 的 Responses 能力矩阵。"""
    if api_type in _NATIVE_API_TYPES:
        return _NATIVE
    if api_type in _BRIDGE_API_TYPES:
        return _BRIDGE
    return _UNSUPPORTED


def is_native_responses_provider(api_type: str) -> bool:
    return api_type in _NATIVE_API_TYPES


def resolve_chat_protocol(
    configured: str,
    *,
    api_type: str,
) -> ChatProtocol:
    """解析模型配置中的 chat_protocol。

    auto：openai/azure 优先 Responses（native 直连；端点未实现 /responses
    时由 LLMClient 记忆 404 并回退 chat_completions），其余 api_type 回退
    chat_completions。显式 responses 即绝对走官方 /responses 接口
    （非 openai 系 api_type 无官方端点，经 litellm bridge 桥接）。
    """
    value = (configured or ChatProtocol.CHAT_COMPLETIONS.value).strip().lower()
    if value not in CHAT_PROTOCOLS:
        raise ValueError(f"无效的 chat_protocol: {configured}")
    protocol = ChatProtocol(value)
    if protocol != ChatProtocol.AUTO:
        return protocol
    if is_native_responses_provider(api_type):
        return ChatProtocol.RESPONSES
    return ChatProtocol.CHAT_COMPLETIONS
