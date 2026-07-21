"""
LLMClient — 统一 LLM 客户端（基于 litellm）。

通过 litellm 统一调用 100+ LLM API，自动处理协议转换：
- openai:    OpenAI 兼容 API（含 MiniMax、硅基流动等）
- anthropic: Anthropic API（含 Claude）
- ollama:    Ollama 本地模型

支持深度思考/推理内容提取（reasoning_content）。
"""

from __future__ import annotations

import json
import os
import re
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional

# 必须在 import litellm 之前设置，阻止启动时拉取远端模型价格表
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import httpx
import litellm

from agent.llm.protocol import CHAT_PROTOCOLS, ChatProtocol, resolve_chat_protocol
from agent.llm.types import (
    ChatResult, ChatStreamDelta, ImageContent, TextCompletionResult, ToolCall, UsageInfo,
)
from core.entity import BaseEntity, EntityType
from core.log import debug, info

litellm.suppress_debug_info = True
litellm.drop_params = True
litellm.local_model_cost_map = True


_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
_SENTINEL = object()
_ANTHROPIC_PROXY_LOCK = asyncio.Lock()


class _ProxyEnvContext:
    """临时设置代理环境变量的上下文管理器，退出时还原原始值。"""

    def __init__(self, proxy_url: str) -> None:
        self._proxy_url = proxy_url
        self._saved: Dict[str, Any] = {}
        self._keys = (
            ("HTTP_PROXY", "HTTPS_PROXY")
            if os.name == "nt"
            else _PROXY_ENV_KEYS
        )

    def __enter__(self) -> "_ProxyEnvContext":
        for k in self._keys:
            self._saved[k] = os.environ.get(k, _SENTINEL)
            os.environ[k] = self._proxy_url
        return self

    def __exit__(self, *exc: Any) -> None:
        for k in self._keys:
            orig = self._saved.get(k, _SENTINEL)
            if orig is _SENTINEL:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig  # type: ignore[assignment]


class _ProxyHttpClient(httpx.AsyncClient):
    """支持 deepcopy 的代理 HTTP 客户端（用于非 Anthropic Provider）。

    继承 httpx.AsyncClient 并覆写 __deepcopy__ 返回自身引用以共享连接池，
    规避 copy.deepcopy 时 _thread.RLock 无法序列化的问题。
    Anthropic 通道因 litellm 内部 JSON 序列化限制，改由环境变量传递代理。
    """

    def __init__(self, proxy_url: str) -> None:
        self._proxy_url = proxy_url
        super().__init__(proxy=proxy_url)

    def __deepcopy__(self, memo: dict) -> "_ProxyHttpClient":
        return self


_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_DEFAULT_API_KEY = ""

API_TYPE_OPENAI = "openai"
API_TYPE_ANTHROPIC = "anthropic"
API_TYPE_OLLAMA = "ollama"
API_TYPE_GEMINI = "gemini"
API_TYPE_AZURE = "azure"
API_TYPE_DEEPSEEK = "deepseek"
API_TYPE_GROQ = "groq"
API_TYPE_BEDROCK = "bedrock"
API_TYPE_VERTEX_AI = "vertex_ai"
API_TYPE_MISTRAL = "mistral"
API_TYPE_COHERE = "cohere"
API_TYPE_HUGGINGFACE = "huggingface"
API_TYPE_CLOUDFLARE = "cloudflare"
API_TYPE_OPENROUTER = "openrouter"
API_TYPE_TOGETHER_AI = "together_ai"
API_TYPE_FIREWORKS_AI = "fireworks_ai"
API_TYPE_PERPLEXITY = "perplexity"
API_TYPE_CEREBRAS = "cerebras"
API_TYPE_XAI = "xai"
API_TYPE_SAMBANOVA = "sambanova"
API_TYPE_VOLCENGINE = "volcengine"
API_TYPE_DASHSCOPE = "dashscope"

API_TYPES = (
    API_TYPE_OPENAI, API_TYPE_ANTHROPIC, API_TYPE_OLLAMA,
    API_TYPE_GEMINI, API_TYPE_AZURE, API_TYPE_DEEPSEEK,
    API_TYPE_GROQ, API_TYPE_BEDROCK, API_TYPE_VERTEX_AI,
    API_TYPE_MISTRAL, API_TYPE_COHERE, API_TYPE_HUGGINGFACE,
    API_TYPE_CLOUDFLARE, API_TYPE_OPENROUTER, API_TYPE_TOGETHER_AI,
    API_TYPE_FIREWORKS_AI, API_TYPE_PERPLEXITY, API_TYPE_CEREBRAS,
    API_TYPE_XAI, API_TYPE_SAMBANOVA, API_TYPE_VOLCENGINE,
    API_TYPE_DASHSCOPE,
)

_LITELLM_PREFIX_MAP: Dict[str, str] = {
    API_TYPE_OPENAI: "openai",
    API_TYPE_ANTHROPIC: "anthropic",
    API_TYPE_OLLAMA: "ollama_chat",
    API_TYPE_GEMINI: "gemini",
    API_TYPE_AZURE: "azure",
    API_TYPE_DEEPSEEK: "deepseek",
    API_TYPE_GROQ: "groq",
    API_TYPE_BEDROCK: "bedrock",
    API_TYPE_VERTEX_AI: "vertex_ai",
    API_TYPE_MISTRAL: "mistral",
    API_TYPE_COHERE: "cohere_chat",
    API_TYPE_HUGGINGFACE: "huggingface",
    API_TYPE_CLOUDFLARE: "cloudflare",
    API_TYPE_OPENROUTER: "openrouter",
    API_TYPE_TOGETHER_AI: "together_ai",
    API_TYPE_FIREWORKS_AI: "fireworks_ai",
    API_TYPE_PERPLEXITY: "perplexity",
    API_TYPE_CEREBRAS: "cerebras",
    API_TYPE_XAI: "xai",
    API_TYPE_SAMBANOVA: "sambanova",
    API_TYPE_VOLCENGINE: "volcengine",
    API_TYPE_DASHSCOPE: "dashscope",
}

_THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_RESERVED_REQUEST_PARAMS = frozenset({
    "model", "messages", "prompt", "input", "tools", "tool_choice",
    "stream", "api_key", "api_base", "http_client", "extra_body",
})


class LLMNotConfiguredError(RuntimeError):
    """未配置可调用模型时抛出的明确异常。"""


class ModelType(str, Enum):
    """模型能力类型。一个客户端可拥有多个类型。"""

    CHAT = "chat"
    VISION = "vision"
    IMAGE_GEN = "image_gen"
    IMAGE_EDIT = "image_edit"
    VIDEO = "video"
    ASR = "asr"
    TTS = "tts"
    EMBEDDING = "embedding"
    RERANK = "rerank"


@dataclass
class LLMClientConfig:
    """LLM 客户端连接与生成参数。"""

    name: str = "default"
    base_url: str = _DEFAULT_BASE_URL
    api_key: str = _DEFAULT_API_KEY
    model: str = ""
    api_type: str = API_TYPE_OPENAI
    temperature: float = 0.7
    top_p: float = 1.0
    # 输出预算上限；None 表示不主动限制，由 provider/SDK 按模型默认决定
    # （Anthropic 协议强制要求该参数，未配置时按模型能力自动推断）。
    max_tokens: Optional[int] = None
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    timeout: float = 120.0
    proxy_url: str = ""
    supports_vision: bool = False
    supports_tools: bool = True
    # 端点是否接受强制工具选择（tool_choice=required/any）；
    # thinking 服务端常开的端点（如 Kimi）应置 False，强制值将降级为 auto
    supports_forced_tool_choice: bool = True
    vision_format: str = "base64"
    model_types: List[str] = field(default_factory=lambda: ["chat"])
    provider_id: str = ""
    supports_reasoning: bool = False
    context_window: int = 0
    request_params: Dict[str, Any] = field(default_factory=dict)
    extra_body: Dict[str, Any] = field(default_factory=dict)
    # 兼容旧配置：历史 extra_params 按 extra_body 处理。
    extra_params: Dict[str, Any] = field(default_factory=dict)
    chat_protocol: str = ChatProtocol.CHAT_COMPLETIONS.value
    # 图片生成协议适配器名（见 agent.llm.image_adapters），空表示按 host 自动匹配。
    media_protocol: str = ""

    def __post_init__(self) -> None:
        if self.api_type not in API_TYPES:
            raise ValueError(f"不支持的 api_type: {self.api_type}")
        if not isinstance(self.model_types, list) or not all(
            isinstance(item, str) and item in {mt.value for mt in ModelType}
            for item in self.model_types
        ):
            raise ValueError(f"无效的 model_types: {self.model_types!r}")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature 必须在 0~2 之间")
        if not 0 <= self.top_p <= 1:
            raise ValueError("top_p 必须在 0~1 之间")
        if self.max_tokens is not None and self.max_tokens < 0:
            raise ValueError("max_tokens 不能小于 0")
        if self.context_window < 0:
            raise ValueError("context_window 不能小于 0")
        if self.timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if self.vision_format not in {"base64", "url", "both"}:
            raise ValueError(f"无效的 vision_format: {self.vision_format}")
        protocol = (self.chat_protocol or ChatProtocol.CHAT_COMPLETIONS.value).strip().lower()
        if protocol not in CHAT_PROTOCOLS:
            raise ValueError(f"无效的 chat_protocol: {self.chat_protocol}")
        self.chat_protocol = protocol
        for name, value in (
            ("request_params", self.request_params),
            ("extra_body", self.extra_body),
            ("extra_params", self.extra_params),
        ):
            if not isinstance(value, dict):
                raise ValueError(f"{name} 必须是对象")
        collisions = _RESERVED_REQUEST_PARAMS.intersection(self.request_params)
        if collisions:
            raise ValueError(f"request_params 不允许覆盖保留参数: {sorted(collisions)}")

    @property
    def effective_proxy(self) -> str:
        """规范化代理地址：纯 ip:port 自动补全 http:// 前缀。"""
        url = self.proxy_url.strip()
        if not url:
            return ""
        if not url.startswith(("http://", "https://", "socks5://", "socks4://")):
            url = f"http://{url}"
        return url

    @property
    def litellm_model(self) -> str:
        """计算 litellm 聊天模型标识符（provider_prefix/model）。"""
        prefix = _LITELLM_PREFIX_MAP.get(self.api_type, "openai")
        if self.model.startswith(f"{prefix}/"):
            return self.model
        return f"{prefix}/{self.model}"

    @property
    def litellm_embed_model(self) -> str:
        """计算 litellm embedding 模型标识符（Ollama 使用 ollama/ 前缀）。"""
        prefix = _LITELLM_PREFIX_MAP.get(self.api_type, "openai")
        if prefix == "ollama_chat":
            prefix = "ollama"
        if self.model.startswith(f"{prefix}/"):
            return self.model
        return f"{prefix}/{self.model}"

    @property
    def use_flat_image_url(self) -> bool:
        """Ollama 兼容端点需要扁平 image_url 格式。"""
        return self.api_type == API_TYPE_OLLAMA

    @property
    def supports_base64_vision(self) -> bool:
        return self.vision_format in ("base64", "both")

    @property
    def supports_url_vision(self) -> bool:
        return self.vision_format in ("url", "both")

    def has_type(self, mt: ModelType) -> bool:
        return mt.value in self.model_types

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "model": self.model,
            "api_type": self.api_type,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "timeout": self.timeout,
            "proxy_url": self.proxy_url,
            "supports_vision": self.supports_vision,
            "supports_tools": self.supports_tools,
            "supports_forced_tool_choice": self.supports_forced_tool_choice,
            "vision_format": self.vision_format,
            "model_types": self.model_types,
            "provider_id": self.provider_id,
            "supports_reasoning": self.supports_reasoning,
            "context_window": self.context_window,
            "request_params": self.request_params,
            "extra_body": self.extra_body,
            "chat_protocol": self.chat_protocol,
            "media_protocol": self.media_protocol,
        }
        if self.extra_params:
            d["extra_params"] = self.extra_params
        return d

    def to_model_dict(self) -> Dict[str, Any]:
        """序列化为供应商-模型层级格式中的模型条目（不含供应商级字段）。"""
        d: Dict[str, Any] = {
            "id": self.name,
            "name": self.name,
            "model": self.model,
            "model_types": self.model_types,
            "supports_vision": self.supports_vision,
            "supports_tools": self.supports_tools,
            "supports_forced_tool_choice": self.supports_forced_tool_choice,
            "vision_format": self.vision_format,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "timeout": self.timeout,
            "supports_reasoning": self.supports_reasoning,
            "context_window": self.context_window,
            "request_params": self.request_params,
            "extra_body": self.extra_body,
            "chat_protocol": self.chat_protocol,
            "media_protocol": self.media_protocol,
        }
        # 输出预算为可选覆盖项：仅在显式配置时写入，避免配置文件冗余
        if self.max_tokens is not None:
            d["max_tokens"] = self.max_tokens
        if self.extra_params:
            d["extra_params"] = self.extra_params
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LLMClientConfig":
        filtered = {}
        for k, v in data.items():
            if k in cls.__dataclass_fields__:
                filtered[k] = v
        if "model_types" not in filtered:
            types = ["chat"]
            if data.get("supports_embedding"):
                types.append("embedding")
            filtered["model_types"] = types
        return cls(**filtered)


class LLMClient(BaseEntity):
    """统一 LLM 客户端（基于 litellm）。

    实现 ChatModel 协议，可直接注入到 Mind。
    litellm 自动处理 OpenAI / Anthropic / Ollama 等协议差异。
    深度思考内容通过 ChatResult.reasoning_content 返回。
    """

    _entity_type = EntityType.MODEL
    _entity_description = "LLM 客户端 (litellm 统一接口)"
    _entity_version = "4.0.0"
    _entity_tags: List[str] = []

    def __init__(self, config: Optional[LLMClientConfig] = None, **kwargs: Any) -> None:
        self.config = config or LLMClientConfig()
        self._entity_tags = [
            "AI Services", "LLM", f"model:{self.config.model}",
        ]
        self._proxy_client: Optional[_ProxyHttpClient] = None
        super().__init__()
        proxy = self.config.effective_proxy
        info(
            f"LLMClient [{self.config.name}] 已创建: "
            f"model={self.config.litellm_model}, "
            f"base_url={self.config.base_url}"
            f"{f', proxy={proxy}' if proxy else ''}",
            tag="模型",
        )

    @property
    def model(self) -> str:
        return self.config.model

    # ------------------------------------------------------------------
    # litellm 调用参数构建
    # ------------------------------------------------------------------

    def _gen_params(self, options: Optional[dict] = None) -> Dict[str, Any]:
        """合并默认生成参数与调用时覆盖。

        Anthropic 不允许 temperature 和 top_p 同时存在，只传 temperature。
        其余不支持的参数由 litellm.drop_params=True 自动处理。
        """
        params: Dict[str, Any] = {"temperature": self.config.temperature}
        if self.config.api_type != API_TYPE_ANTHROPIC:
            params["top_p"] = self.config.top_p
        if self.config.max_tokens and self.config.max_tokens > 0:
            params["max_tokens"] = self.config.max_tokens
        elif self.config.api_type == API_TYPE_ANTHROPIC:
            # Anthropic 协议强制要求 max_tokens：未显式配置时按模型能力推断
            params["max_tokens"] = self._infer_anthropic_max_tokens()
        if self.config.frequency_penalty:
            params["frequency_penalty"] = self.config.frequency_penalty
        if self.config.presence_penalty:
            params["presence_penalty"] = self.config.presence_penalty
        if options:
            params.update(options)
        if self.config.api_type == API_TYPE_ANTHROPIC:
            params.pop("top_p", None)
        return params

    def _anthropic_proxy_ctx(self) -> _ProxyEnvContext | None:
        """Anthropic 专用：返回临时代理环境变量上下文，无代理时返回 None。"""
        if self.config.api_type != API_TYPE_ANTHROPIC:
            return None
        proxy = self.config.effective_proxy
        return _ProxyEnvContext(proxy) if proxy else None

    # Anthropic 自定义/未知模型查不到输出上限时的兜底预算（保守值，避免超出端点限制）
    _ANTHROPIC_FALLBACK_MAX_TOKENS = 16384

    def _infer_anthropic_max_tokens(self) -> int:
        """推断 Anthropic 输出预算：仅信 litellm 模型信息中的 max_output_tokens。

        不能用 max_tokens 键——它是上下文窗口（自定义模型注册时即按 context_window
        写入），当作输出预算会超出端点限制导致 400。查不到时回落保守兜底值。
        """
        try:
            info = self.get_model_info(self.config.litellm_model)
            cap = info.get("max_output_tokens")
            if cap:
                return int(cap)
        except Exception:
            pass
        if not getattr(self, "_fallback_budget_logged", False):
            self._fallback_budget_logged = True
            debug(
                f"LLMClient [{self.config.name}] 未配置 max_tokens 且查不到模型输出上限，"
                f"使用兜底值 {self._ANTHROPIC_FALLBACK_MAX_TOKENS}（可在模型配置中显式指定）",
                tag="模型",
            )
        return self._ANTHROPIC_FALLBACK_MAX_TOKENS

    def _get_proxy_client(self) -> Optional[_ProxyHttpClient]:
        """按需返回当前 Provider 的代理客户端（懒初始化）。"""
        proxy = self.config.effective_proxy
        if not proxy:
            return None
        if self._proxy_client is None or self._proxy_client.is_closed:
            self._proxy_client = _ProxyHttpClient(proxy)
        return self._proxy_client

    def _build_kwargs(
            self,
            messages: list[dict],
            options: Optional[dict] = None,
            tools: Optional[list[dict]] = None,
            tool_choice: Optional[Any] = None,
            *,
            stream: bool = False,
    ) -> Dict[str, Any]:
        """构建 litellm 调用参数。"""
        self._ensure_configured()
        params = self._gen_params(options)
        effort = params.pop("reasoning_effort", None)
        if effort is not None:
            effort = str(effort).strip().lower()
            if effort not in {"low", "medium", "high", "max"}:
                raise ValueError(f"无效的 reasoning_effort: {effort}")
        adapted = self._adapt_messages(messages)

        kwargs: Dict[str, Any] = {
            "model": self.config.litellm_model,
            "messages": adapted,
            "timeout": self.config.timeout,
            **params,
        }
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if tools:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = self._resolve_tool_choice(tool_choice)
        if stream:
            kwargs["stream"] = True

        proxy_url = self.config.effective_proxy
        if proxy_url and self.config.api_type != API_TYPE_ANTHROPIC:
            proxy_client = self._get_proxy_client()
            if proxy_client:
                kwargs["http_client"] = proxy_client

        reserved = {
            "model", "messages", "prompt", "input", "tools", "tool_choice",
            "stream", "api_key", "api_base", "http_client", "extra_body",
        }
        self._merge_request_params(kwargs, reserved)

        extra = dict(self.config.extra_params)
        extra.update(self.config.extra_body)
        if self.config.supports_reasoning and self.config.api_type != API_TYPE_ANTHROPIC:
            extra.setdefault("reasoning_split", True)
        if extra:
            kwargs["extra_body"] = extra

        if effort and self._supports_effort():
            kwargs["reasoning_effort"] = effort
            if self.config.api_type == API_TYPE_ANTHROPIC:
                kwargs["temperature"] = 1
        elif effort:
            raise ValueError(
                f"当前 provider 不支持 reasoning_effort: {self.config.api_type}"
            )

        return kwargs

    def _ensure_configured(self) -> None:
        if not self.config.model.strip():
            raise LLMNotConfiguredError("尚未配置可用的 LLM 模型")

    def _resolve_tool_choice(self, tool_choice: Any) -> Any:
        """端点不接受强制工具选择时，将强制值降级为 auto。

        强制值包括字符串 required 与指定工具的 object 形式
        （OpenAI {"type": "function"} / Anthropic {"type": "any"|"tool"}）。
        auto / none 与 thinking 模式兼容，原样保留。
        """
        if self.config.supports_forced_tool_choice:
            return tool_choice
        if isinstance(tool_choice, str):
            return tool_choice if tool_choice in ("auto", "none") else "auto"
        if isinstance(tool_choice, dict):
            return tool_choice if tool_choice.get("type") in ("auto", "none") else "auto"
        return tool_choice

    def _merge_request_params(
        self,
        kwargs: Dict[str, Any],
        reserved: set[str],
    ) -> None:
        collisions = reserved.intersection(self.config.request_params)
        if collisions:
            raise ValueError(f"request_params 不允许覆盖保留参数: {sorted(collisions)}")
        kwargs.update(self.config.request_params)

    def _supports_effort(self) -> bool:
        """检查当前模型是否支持 reasoning_effort 参数。"""
        if self.config.supports_reasoning:
            return True
        try:
            return bool(litellm.supports_reasoning(self.config.litellm_model))
        except Exception:
            return False

    def _adapt_messages(self, messages: list[dict]) -> list[dict]:
        """合并头部连续 system 消息为一条，非头部 system 转 user。"""
        head_systems: list[Any] = []
        rest_start = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                head_systems.append(msg.get("content", ""))
                rest_start = i + 1
            else:
                break

        adapted: list[dict] = []
        if head_systems:
            if all(isinstance(item, str) for item in head_systems):
                merged: Any = "\n\n".join(item for item in head_systems if item)
            else:
                merged_parts: list[dict[str, Any]] = []
                for item in head_systems:
                    if isinstance(item, str):
                        if item:
                            merged_parts.append({"type": "text", "text": item})
                    elif isinstance(item, list):
                        merged_parts.extend(
                            part for part in item if isinstance(part, dict)
                        )
                merged = merged_parts
            adapted.append({"role": "system", "content": merged})

        for msg in messages[rest_start:]:
            if msg.get("role") == "system":
                adapted.append({**msg, "role": "user"})
            else:
                adapted.append(msg)

        return adapted

    # ------------------------------------------------------------------
    # ChatModel 协议：chat
    # ------------------------------------------------------------------

    @property
    def resolved_chat_protocol(self) -> ChatProtocol:
        """解析当前模型实际使用的对话协议。"""
        return resolve_chat_protocol(
            self.config.chat_protocol,
            api_type=self.config.api_type,
        )

    def responses_client(self) -> Any:
        """构建绑定当前配置的 ResponsesClient。"""
        from agent.llm.responses.client import ResponsesClient

        self._ensure_configured()
        http_client = None
        if self.config.api_type != API_TYPE_ANTHROPIC:
            http_client = self._get_proxy_client()
        return ResponsesClient(
            model=self.config.litellm_model,
            api_type=self.config.api_type,
            api_base=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.timeout,
            request_params=self.config.request_params,
            extra_body={**self.config.extra_params, **self.config.extra_body},
            prefer_bridge_for_custom=True,
            http_client=http_client,
        )

    async def responses_create(self, **kwargs: Any) -> Any:
        """创建 Responses 调用。"""
        client = self.responses_client()
        return await self._call_with_proxy(client.create(**kwargs))

    async def responses_stream(self, **kwargs: Any) -> AsyncGenerator[Any, None]:
        """流式 Responses 调用。"""
        client = self.responses_client()
        ctx = self._anthropic_proxy_ctx()
        lock_acquired = False
        try:
            if ctx:
                await _ANTHROPIC_PROXY_LOCK.acquire()
                lock_acquired = True
                with ctx:
                    async for event in client.stream(**kwargs):
                        yield event
            else:
                async for event in client.stream(**kwargs):
                    yield event
        finally:
            if lock_acquired:
                _ANTHROPIC_PROXY_LOCK.release()

    async def responses_get(self, response_id: str) -> Any:
        client = self.responses_client()
        return await self._call_with_proxy(client.get(response_id))

    async def responses_delete(self, response_id: str) -> Dict[str, Any]:
        client = self.responses_client()
        return await self._call_with_proxy(client.delete(response_id))

    async def responses_cancel(self, response_id: str) -> Any:
        client = self.responses_client()
        return await self._call_with_proxy(client.cancel(response_id))

    async def responses_compact(self, **kwargs: Any) -> Any:
        client = self.responses_client()
        return await self._call_with_proxy(client.compact(**kwargs))

    async def _call_with_proxy(self, awaitable: Any) -> Any:
        ctx = self._anthropic_proxy_ctx()
        if ctx:
            async with _ANTHROPIC_PROXY_LOCK:
                with ctx:
                    return await awaitable
        return await awaitable

    async def _chat_via_responses(
            self,
            messages: list[dict],
            *,
            options: Optional[dict] = None,
            tools: Optional[list[dict]] = None,
            tool_choice: Optional[Any] = None,
    ) -> ChatResult:
        from agent.llm.responses.client import convert_chat_tools, messages_to_responses_input

        adapted = self._adapt_messages(messages)
        instructions, input_payload = messages_to_responses_input(adapted)
        params = self._gen_params(options)
        effort = params.pop("reasoning_effort", None)
        if effort is not None:
            effort = str(effort).strip().lower()
            if effort not in {"low", "medium", "high", "max"}:
                raise ValueError(f"无效的 reasoning_effort: {effort}")
            if not self._supports_effort():
                raise ValueError(
                    f"当前 provider 不支持 reasoning_effort: {self.config.api_type}"
                )
        create_kwargs: Dict[str, Any] = {
            "input": input_payload,
            "instructions": instructions,
            "tools": convert_chat_tools(tools),
            "tool_choice": tool_choice,
            "temperature": params.get("temperature"),
            "top_p": params.get("top_p"),
            "max_output_tokens": params.get("max_tokens"),
        }
        if effort:
            create_kwargs["extra"] = {"reasoning": {"effort": effort}}
        debug(
            f"LLM chat(via responses): {self.config.litellm_model}, msgs={len(adapted)}",
            tag="模型",
        )
        result = await self.responses_create(**create_kwargs)
        return result.to_chat_result()

    async def chat(
            self,
            messages: list[dict],
            *,
            options: Optional[dict] = None,
            tools: Optional[list[dict]] = None,
            tool_choice: Optional[Any] = None,
    ) -> ChatResult:
        """非流式聊天补全（通过 litellm 统一路由）。"""
        if self.resolved_chat_protocol == ChatProtocol.RESPONSES:
            return await self._chat_via_responses(
                messages,
                options=options,
                tools=tools,
                tool_choice=tool_choice,
            )
        kwargs = self._build_kwargs(messages, options, tools, tool_choice)
        debug(
            f"LLM chat: {self.config.litellm_model}, msgs={len(kwargs['messages'])}",
            tag="模型",
        )
        ctx = self._anthropic_proxy_ctx()
        if ctx:
            async with _ANTHROPIC_PROXY_LOCK:
                with ctx:
                    resp = await litellm.acompletion(**kwargs)
        else:
            resp = await litellm.acompletion(**kwargs)
        return self._parse_response(resp)

    # ------------------------------------------------------------------
    # ChatModel 协议：chat_stream
    # ------------------------------------------------------------------

    async def chat_stream(
            self,
            messages: list[dict],
            *,
            options: Optional[dict] = None,
            tools: Optional[list[dict]] = None,
            tool_choice: Optional[Any] = None,
    ) -> AsyncGenerator[ChatStreamDelta, None]:
        """流式聊天补全（通过 litellm 统一路由）。

        支持流式 tool_calls 累积：各 chunk 的 tool_call 片段会被合并，
        在 finish_reason 为 "tool_calls" 或 "stop" 时随最终 delta 输出。
        最后一个 chunk 的 usage 也会被提取。
        """
        kwargs = self._build_kwargs(messages, options, tools, tool_choice, stream=True)
        kwargs["stream_options"] = {"include_usage": True}
        ctx = self._anthropic_proxy_ctx()
        lock_acquired = False
        stream: Any = None
        reasoning_buf = ""
        tc_bufs: Dict[int, Dict[str, str]] = {}
        try:
            if ctx:
                await _ANTHROPIC_PROXY_LOCK.acquire()
                lock_acquired = True
                with ctx:
                    stream = await litellm.acompletion(**kwargs)
                    async for item in self._iter_stream(stream, reasoning_buf, tc_bufs):
                        reasoning_buf = item[1]
                        yield item[0]
            else:
                stream = await litellm.acompletion(**kwargs)
                async for item in self._iter_stream(stream, reasoning_buf, tc_bufs):
                    reasoning_buf = item[1]
                    yield item[0]
        finally:
            if stream is not None:
                close_fn = getattr(stream, "aclose", None)
                if close_fn:
                    await close_fn()
            if lock_acquired:
                _ANTHROPIC_PROXY_LOCK.release()

        if tc_bufs:
            yield ChatStreamDelta(
                tool_calls=self._complete_tool_buffers(tc_bufs),
                finish_reason="tool_calls",
            )

    async def _iter_stream(
        self,
        stream: Any,
        reasoning_buf: str,
        tc_bufs: Dict[int, Dict[str, str]],
    ) -> AsyncGenerator[tuple[ChatStreamDelta, str], None]:
        """解析 LiteLLM 流，并保留跨 chunk 的工具与推理缓冲。"""
        async for chunk in stream:
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                stream_usage = self._usage_from_object(getattr(chunk, "usage", None))
                if stream_usage:
                    yield ChatStreamDelta(usage=stream_usage), reasoning_buf
                continue
            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue
            content = getattr(delta, "content", None) or ""
            finish = getattr(choice, "finish_reason", None) or ""
            reasoning = ""
            rc = getattr(delta, "reasoning_content", None)
            if isinstance(rc, str) and rc:
                reasoning = rc
            else:
                for detail in getattr(delta, "reasoning_details", None) or []:
                    text = detail.get("text", "") if isinstance(detail, dict) else getattr(detail, "text", "")
                    if text and len(text) > len(reasoning_buf):
                        reasoning = text[len(reasoning_buf):]
                        reasoning_buf = text

            for tc_chunk in getattr(delta, "tool_calls", None) or []:
                # 部分 provider 会把 index 返回为字符串，统一强转 int，
                # 避免混合类型 key 在 sorted() 时炸 TypeError
                idx = self._normalize_tc_index(
                    getattr(tc_chunk, "index", None), len(tc_bufs)
                )
                buf = tc_bufs.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                tc_id = getattr(tc_chunk, "id", None)
                if tc_id:
                    buf["id"] = tc_id
                func = getattr(tc_chunk, "function", None)
                if func:
                    if getattr(func, "name", None):
                        buf["name"] = func.name
                    arguments = getattr(func, "arguments", None)
                    if arguments:
                        buf["arguments"] += str(arguments)

            completed_tools = self._complete_tool_buffers(tc_bufs) if finish and tc_bufs else []
            if completed_tools:
                tc_bufs.clear()
            yield ChatStreamDelta(
                content=content,
                tool_calls=completed_tools,
                finish_reason=finish,
                reasoning_content=reasoning,
                usage=self._usage_from_object(getattr(chunk, "usage", None)),
            ), reasoning_buf

    @staticmethod
    def _normalize_tc_index(raw: Any, fallback: int) -> int:
        """将流式 tool_call 的 index 归一化为 int，非法值回退为 fallback。"""
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _complete_tool_buffers(
        tc_bufs: Dict[int, Dict[str, str]],
    ) -> list[ToolCall]:
        result: list[ToolCall] = []
        for _, buf in sorted(tc_bufs.items()):
            if not buf["name"]:
                continue
            result.append(ToolCall(
                id=buf["id"] or f"tc_{len(result)}",
                name=buf["name"],
                arguments=buf["arguments"],
            ))
        return result

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------

    def _parse_response(self, resp: Any) -> ChatResult:
        """将 litellm 统一响应解析为 ChatResult（含 usage）。"""
        choices = getattr(resp, "choices", None) or []
        raw_dict: Optional[dict] = resp.model_dump() if hasattr(resp, "model_dump") else None
        if not choices:
            return ChatResult(
                content="",
                finish_reason="error",
                raw=raw_dict,
                usage=self._extract_usage(resp),
                model=getattr(resp, "model", "") or "",
            )
        choice = choices[0]
        msg = getattr(choice, "message", None)
        if msg is None:
            return ChatResult(
                content="",
                finish_reason=getattr(choice, "finish_reason", None) or "error",
                raw=raw_dict,
                usage=self._extract_usage(resp),
                model=getattr(resp, "model", "") or "",
            )

        usage = self._extract_usage(resp)

        return ChatResult(
            content=msg.content or "",
            tool_calls=self._parse_tool_calls(getattr(msg, "tool_calls", None)),
            finish_reason=getattr(choice, "finish_reason", None) or "",
            reasoning_content=self._extract_reasoning(msg, raw_dict),
            raw=raw_dict,
            usage=usage,
            model=getattr(resp, "model", "") or "",
        )

    @staticmethod
    def _extract_usage(resp: Any) -> Optional[UsageInfo]:
        """从 litellm 响应中提取 token 用量。"""
        return LLMClient._usage_from_object(getattr(resp, "usage", None))

    @staticmethod
    def _usage_from_object(usage: Any) -> Optional[UsageInfo]:
        if not usage:
            return None
        result = UsageInfo(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )
        return result if result.total_tokens or result.prompt_tokens or result.completion_tokens else None

    @staticmethod
    def _extract_reasoning(msg: Any, raw_response: Optional[dict] = None) -> str:
        """从响应中提取推理内容。

        支持三种来源（按优先级）：
        1. reasoning_content 字段（litellm 标准，Anthropic thinking blocks）
        2. reasoning_details 字段（自定义累积格式）
        3. <think> 标签（DeepSeek 等模型）
        """
        rc = getattr(msg, "reasoning_content", None)
        if rc and isinstance(rc, str):
            return rc

        details = getattr(msg, "reasoning_details", None)
        if not details and raw_response:
            choices = raw_response.get("choices", [])
            if choices:
                msg_dict = choices[0].get("message", {})
                if msg_dict.get("reasoning_content"):
                    return str(msg_dict["reasoning_content"])
                details = msg_dict.get("reasoning_details")

        if details:
            parts: list[str] = []
            for d in details:
                text = d.get("text", "") if isinstance(d, dict) else getattr(d, "text", "")
                if text:
                    parts.append(text)
            if parts:
                return "\n".join(parts)

        content = getattr(msg, "content", "") or ""
        m = _THINK_TAG_RE.search(content)
        return m.group(1).strip() if m else ""

    @staticmethod
    def _parse_tool_calls(raw_tool_calls: Any) -> list[ToolCall]:
        if not raw_tool_calls:
            return []
        result: list[ToolCall] = []
        for i, tc in enumerate(raw_tool_calls):
            func = getattr(tc, "function", None)
            if func is None:
                continue
            args_str = func.arguments if isinstance(func.arguments, str) else json.dumps(
                func.arguments, ensure_ascii=False,
            )
            result.append(ToolCall(
                id=getattr(tc, "id", None) or f"tc_{i}",
                name=getattr(func, "name", None) or "",
                arguments=args_str,
                raw=tc.model_dump() if hasattr(tc, "model_dump") else {},
            ))
        return result

    # ------------------------------------------------------------------
    # 多模态消息处理
    # ------------------------------------------------------------------

    async def describe_images(
            self,
            images: list[ImageContent],
            prompt: str = "请简要描述这些图片的内容。",
    ) -> str:
        from agent.llm.image_utils import build_multimodal_content
        content = build_multimodal_content(
            prompt, images, flat_url=self.config.use_flat_image_url,
        )
        messages: list[dict] = [{"role": "user", "content": content}]
        result = await self.chat(messages, options={"max_tokens": 1024})
        text = (result.content or "").strip()
        if not text:
            # 空结果视为调用失败，让上层回退到下一个视觉模型
            raise RuntimeError("视觉模型返回空结果")
        return text

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本嵌入（通过 litellm 统一路由）。"""
        self._ensure_configured()
        kwargs: Dict[str, Any] = {
            "model": self.config.litellm_embed_model,
            "input": texts,
            "timeout": self.config.timeout,
            "encoding_format": "float",
        }
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        self._merge_request_params(
            kwargs,
            {"model", "input", "api_key", "api_base", "http_client", "extra_body"},
        )
        body = dict(self.config.extra_params)
        body.update(self.config.extra_body)
        if body:
            kwargs["extra_body"] = body
        proxy_client = self._get_proxy_client()
        if proxy_client and self.config.api_type != API_TYPE_ANTHROPIC:
            kwargs["http_client"] = proxy_client
        ctx = self._anthropic_proxy_ctx()
        if ctx:
            async with _ANTHROPIC_PROXY_LOCK:
                with ctx:
                    resp = await litellm.aembedding(**kwargs)
        else:
            resp = await litellm.aembedding(**kwargs)
        return [item["embedding"] for item in resp.data]

    # ------------------------------------------------------------------
    # Text Completion（/completions 端点）
    # ------------------------------------------------------------------

    async def text_completion(
            self,
            prompt: str,
            *,
            options: Optional[dict] = None,
    ) -> TextCompletionResult:
        """文本补全（通过 litellm.atext_completion）。"""
        self._ensure_configured()
        params = self._gen_params(options)
        kwargs: Dict[str, Any] = {
            "model": self.config.litellm_model,
            "prompt": prompt,
            "timeout": self.config.timeout,
            **params,
        }
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key

        self._merge_request_params(
            kwargs,
            {"model", "prompt", "api_key", "api_base", "http_client", "extra_body"},
        )
        body = dict(self.config.extra_params)
        body.update(self.config.extra_body)
        if body:
            kwargs["extra_body"] = body
        proxy_client = self._get_proxy_client()
        if proxy_client and self.config.api_type != API_TYPE_ANTHROPIC:
            kwargs["http_client"] = proxy_client
        ctx = self._anthropic_proxy_ctx()
        if ctx:
            async with _ANTHROPIC_PROXY_LOCK:
                with ctx:
                    resp = await litellm.atext_completion(**kwargs)
        else:
            resp = await litellm.atext_completion(**kwargs)
        choices = getattr(resp, "choices", None) or []
        choice = choices[0] if choices else None
        usage = self._extract_usage(resp)
        raw_dict: Optional[dict] = resp.model_dump() if hasattr(resp, "model_dump") else None

        return TextCompletionResult(
            text=getattr(choice, "text", None) or "",
            finish_reason=getattr(choice, "finish_reason", None) or ("error" if choice is None else ""),
            usage=usage,
            raw=raw_dict,
        )

    # ------------------------------------------------------------------
    # Token 计数与模型信息工具
    # ------------------------------------------------------------------

    @staticmethod
    def count_tokens(model: str, messages: list[dict]) -> int:
        """计算消息列表的 token 数（基于模型的 tokenizer）。"""
        try:
            return litellm.token_counter(model=model, messages=messages)
        except Exception:
            return 0

    @staticmethod
    def count_text_tokens(model: str, text: str) -> int:
        """计算纯文本的 token 数。"""
        try:
            return litellm.token_counter(model=model, text=text)
        except Exception:
            return 0

    @staticmethod
    def get_max_tokens(model: str) -> Optional[int]:
        """查询模型的最大上下文 token 数。"""
        try:
            return litellm.get_max_tokens(model)
        except Exception:
            return None

    @staticmethod
    def get_model_info(model: str) -> Dict[str, Any]:
        """查询模型完整信息（上下文窗口 / 输出上限 / 能力 / 价格）。"""
        try:
            return litellm.get_model_info(model)
        except Exception:
            return {}

    @staticmethod
    def get_model_cost(model: str) -> Optional[Dict[str, Any]]:
        """查询模型的价格信息（input_cost_per_token / output_cost_per_token 等）。"""
        return litellm.model_cost.get(model)

    # ------------------------------------------------------------------
    # 能力探测
    # ------------------------------------------------------------------

    @staticmethod
    def _make_test_png(size: int = 64) -> bytes:
        import struct
        import zlib as _zlib

        def _chunk(chunk_type: bytes, data: bytes) -> bytes:
            crc = _zlib.crc32(chunk_type + data) & 0xFFFFFFFF
            return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

        ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
        scanline = b"\x00" + (b"\xff\x00\x00" * size)
        raw_data = scanline * size
        idat = _zlib.compress(raw_data)
        return (
                b"\x89PNG\r\n\x1a\n"
                + _chunk(b"IHDR", ihdr)
                + _chunk(b"IDAT", idat)
                + _chunk(b"IEND", b"")
        )

    @staticmethod
    async def probe_capabilities(
            base_url: str,
            api_key: str,
            model: str,
            api_type: str = API_TYPE_OLLAMA,
            timeout: float = 120.0,
    ) -> Dict[str, Any]:
        """探测模型是否支持 tools 和 vision（通过 litellm）。"""
        prefix = _LITELLM_PREFIX_MAP.get(api_type, "openai")
        litellm_model = f"{prefix}/{model}"
        flat_url = (api_type == API_TYPE_OLLAMA)

        probe_kw: Dict[str, Any] = {
            "api_base": base_url,
            "api_key": api_key or _DEFAULT_API_KEY,
            "timeout": timeout,
            "temperature": 0.7,
        }

        result: Dict[str, Any] = {
            "supports_tools": False,
            "tools_detail": "",
            "supports_vision": False,
            "vision_detail": "",
        }

        result.update(await LLMClient._probe_tools(litellm_model, probe_kw))
        result.update(await LLMClient._probe_vision(litellm_model, probe_kw, flat_url, api_type))
        return result

    @staticmethod
    async def _probe_tools(
            litellm_model: str, probe_kw: Dict[str, Any],
    ) -> Dict[str, Any]:
        test_tool = [{
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "获取当前时间",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        try:
            resp = await litellm.acompletion(
                model=litellm_model,
                messages=[{"role": "user", "content": "现在几点了？请调用工具获取。"}],
                tools=test_tool,
                tool_choice="auto",
                max_tokens=2048,
                **probe_kw,
            )
            has_calls = bool(resp.choices[0].message.tool_calls)
            return {
                "supports_tools": True,
                "tools_detail": (
                    "模型返回了 tool_calls，支持原生工具调用"
                    if has_calls
                    else "请求成功（模型接受了 tools 参数）"
                ),
            }
        except Exception as exc:
            status = getattr(exc, "status_code", "")
            detail = f"不支持 (HTTP {status})" if status else f"检测失败: {exc}"
            return {"supports_tools": False, "tools_detail": detail}

    _BASE64_ONLY_TYPES = frozenset({API_TYPE_OLLAMA})

    @staticmethod
    async def _probe_vision(
            litellm_model: str, probe_kw: Dict[str, Any],
            flat_url: bool, api_type: str,
    ) -> Dict[str, Any]:
        import base64

        b64_img = base64.b64encode(LLMClient._make_test_png()).decode()
        data_uri = f"data:image/png;base64,{b64_img}"
        img_value: Any = data_uri if flat_url else {"url": data_uri}
        vision_content: list[dict] = [
            {"type": "text", "text": "这张图片是什么颜色？用一个词回答。"},
            {"type": "image_url", "image_url": img_value},
        ]
        try:
            resp = await litellm.acompletion(
                model=litellm_model,
                messages=[{"role": "user", "content": vision_content}],
                max_tokens=256,
                **probe_kw,
            )
            answer = resp.choices[0].message.content or ""
            fmt = "base64" if api_type in LLMClient._BASE64_ONLY_TYPES else "both"
            return {
                "supports_vision": True,
                "vision_detail": f"模型正确处理了图片输入: \"{answer[:80]}\"",
                "vision_format": fmt,
            }
        except Exception as exc:
            status = getattr(exc, "status_code", "")
            detail = f"不支持 (HTTP {status})" if status else f"不支持: {exc}"
            return {"supports_vision": False, "vision_detail": detail}

    # ------------------------------------------------------------------
    # 客户端生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭按客户端持有的代理连接池。"""
        client = self._proxy_client
        self._proxy_client = None
        if client is not None and not client.is_closed:
            await client.aclose()

    def update_config(self, **kwargs: Any) -> None:
        old_proxy = self.config.effective_proxy
        original = {
            k: getattr(self.config, k)
            for k in kwargs
            if hasattr(self.config, k)
        }
        try:
            for k, v in kwargs.items():
                if hasattr(self.config, k):
                    setattr(self.config, k, v)
            self.config.__post_init__()
        except Exception:
            for k, v in original.items():
                setattr(self.config, k, v)
            raise
        if self.config.effective_proxy != old_proxy and self._proxy_client is not None:
            stale_client = self._proxy_client
            self._proxy_client = None
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(stale_client.aclose())
            except RuntimeError:
                # 无运行事件循环时，下次生命周期关闭仍无法复用旧客户端；
                # httpx 会在对象回收时释放底层资源。
                pass
        info(f"LLMClient [{self.config.name}] 配置已更新", tag="模型")

    def __repr__(self) -> str:
        return (
            f"LLMClient(name={self.config.name!r}, "
            f"model={self.config.litellm_model!r}, "
            f"base_url={self.config.base_url!r})"
        )
