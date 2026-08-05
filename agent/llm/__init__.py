"""LLM: unified model interface (OpenAI-compatible API) + media client."""

import os as _os

# 必须在任何 litellm 导入之前设置，阻止启动时拉取远端模型价格表
# （包初始化先于一切 agent.llm.* 子模块执行，litellm 的模块级导入全部集中在本包内）
_os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from .base import ChatModel
from .llm_client import (
    API_TYPE_ANTHROPIC,
    API_TYPE_OLLAMA,
    API_TYPE_OPENAI,
    API_TYPES,
    LLMClient,
    LLMClientConfig,
    LLMNotConfiguredError,
    ModelType,
)
from .llm_manager import LLMManager, ProviderConfig, get_llm_manager
from .media_client import MediaClient
from .types import (
    ChatResult,
    ChatStreamDelta,
    ImageContent,
    MessageContent,
    TextCompletionResult,
    ToolCall,
    UsageInfo,
    VideoContent,
)

__all__ = [
    "API_TYPE_ANTHROPIC",
    "API_TYPE_OLLAMA",
    "API_TYPE_OPENAI",
    "API_TYPES",
    "ChatModel",
    "ChatResult",
    "ChatStreamDelta",
    "ImageContent",
    "MediaClient",
    "MessageContent",
    "ModelType",
    "TextCompletionResult",
    "ToolCall",
    "UsageInfo",
    "VideoContent",
    "LLMClient",
    "LLMClientConfig",
    "LLMNotConfiguredError",
    "LLMManager",
    "ProviderConfig",
    "get_llm_manager",
]
