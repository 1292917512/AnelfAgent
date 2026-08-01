"""LLM 客户端配置：LLMClientConfig、API 类型常量与 litellm 前缀映射。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from agent.llm.protocol import CHAT_PROTOCOLS, ChatProtocol
from agent.llm.reasoning import normalize_effort
from core.log import log

_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_DEFAULT_API_KEY = ""
# 全局默认请求超时（秒）；模型配置仅在需要非默认值时才显式指定。
# 取值须明显小于思维循环总预算（mind.llm_timeout，默认 120s），
# 否则单次慢调用即耗尽共享预算，重试与回退模型永远无法执行
DEFAULT_TIMEOUT = 60.0

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

_RESERVED_REQUEST_PARAMS = frozenset({
    "model", "messages", "prompt", "input", "tools", "tool_choice",
    "stream", "api_key", "api_base", "http_client", "extra_body",
})


class ModelType(str, Enum):
    """模型能力类型。一个客户端可拥有多个类型。"""

    CHAT = "chat"
    VISION = "vision"
    IMAGE_GEN = "image_gen"
    IMAGE_EDIT = "image_edit"
    VIDEO = "video"
    ASR = "asr"
    TTS = "tts"
    MUSIC = "music"
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
    # None 表示不下发，由 provider/SDK 按模型默认决定（参考 hermes/nekro/openclaw）
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    # 输出预算上限；None 表示不主动限制，由 provider/SDK 按模型默认决定
    # （Anthropic 协议强制要求该参数，未配置时按模型能力自动推断）。
    max_tokens: Optional[int] = None
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    timeout: float = DEFAULT_TIMEOUT
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
    # 每模型专属思考等级（off/minimal/low/medium/high/xhigh/max）；
    # 空=跟随全局/任务注入的等级。非法值在 __post_init__ 归一为 ""
    reasoning_effort: str = ""
    context_window: int = 0
    # embedding 请求的目标维度（Matryoshka 可变维度模型生效）；0 = 模型默认维度
    embedding_dims: int = 0
    # embedding 单批文本数上限（供应商限制，如 text-embedding-v4 限 10）；0 = 不限制
    embedding_max_batch: int = 0
    request_params: Dict[str, Any] = field(default_factory=dict)
    extra_body: Dict[str, Any] = field(default_factory=dict)
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
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("temperature 必须在 0~2 之间")
        if self.top_p is not None and not 0 <= self.top_p <= 1:
            raise ValueError("top_p 必须在 0~1 之间")
        if self.max_tokens is not None and self.max_tokens < 0:
            raise ValueError("max_tokens 不能小于 0")
        if self.context_window < 0:
            raise ValueError("context_window 不能小于 0")
        if self.embedding_dims < 0:
            raise ValueError("embedding_dims 不能小于 0")
        if self.embedding_max_batch < 0:
            raise ValueError("embedding_max_batch 不能小于 0")
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
        normalized_effort = normalize_effort(self.reasoning_effort)
        if self.reasoning_effort and not normalized_effort:
            log(
                f"模型 [{self.name}] 配置了无效的 reasoning_effort="
                f"{self.reasoning_effort!r}，已重置为跟随全局",
                "WARNING", tag="模型",
            )
        self.reasoning_effort = normalized_effort
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
            "supports_reasoning": self.supports_reasoning,
            "context_window": self.context_window,
            "request_params": self.request_params,
            "extra_body": self.extra_body,
            "chat_protocol": self.chat_protocol,
            "media_protocol": self.media_protocol,
        }
        # 采样/超时参数为可选覆盖项：仅在显式配置（非默认）时写入，避免配置文件冗余
        if self.temperature is not None:
            d["temperature"] = self.temperature
        if self.top_p is not None:
            d["top_p"] = self.top_p
        if self.max_tokens is not None:
            d["max_tokens"] = self.max_tokens
        if self.embedding_dims:
            d["embedding_dims"] = self.embedding_dims
        if self.embedding_max_batch:
            d["embedding_max_batch"] = self.embedding_max_batch
        if self.frequency_penalty:
            d["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty:
            d["presence_penalty"] = self.presence_penalty
        if self.timeout != DEFAULT_TIMEOUT:
            d["timeout"] = self.timeout
        if self.extra_params:
            d["extra_params"] = self.extra_params
        if self.reasoning_effort:
            d["reasoning_effort"] = self.reasoning_effort
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
