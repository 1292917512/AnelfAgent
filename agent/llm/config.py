"""LLM 客户端配置：LLMClientConfig、API 类型常量与 litellm 前缀映射。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from agent.llm.protocol import CHAT_PROTOCOLS, ChatProtocol
from agent.llm.reasoning import normalize_effort, parse_thinking_spec
from core.log import log

_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_DEFAULT_API_KEY = ""
# 全局默认请求超时（秒）；模型配置仅在需要非默认值时才显式指定。
# 这是单次尝试的挂起护栏：chat_with_fallback 的候选预算按
# 客户端超时 × (重试次数+1) 派生，慢调用不再挤占重试与回退空间
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
    "extra_headers",
})

# 各 api_type 的默认接口地址（留空 base_url 时的兜底，供 UI 与远程拉取共用）
DEFAULT_BASE_URLS: Dict[str, str] = {
    API_TYPE_OPENAI: "https://api.openai.com/v1",
    API_TYPE_ANTHROPIC: "https://api.anthropic.com/v1",
    API_TYPE_GEMINI: "https://generativelanguage.googleapis.com/v1beta",
    API_TYPE_OLLAMA: "http://127.0.0.1:11434/v1",
    API_TYPE_DEEPSEEK: "https://api.deepseek.com/v1",
    API_TYPE_OPENROUTER: "https://openrouter.ai/api/v1",
    API_TYPE_GROQ: "https://api.groq.com/openai/v1",
    API_TYPE_XAI: "https://api.x.ai/v1",
    API_TYPE_DASHSCOPE: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    API_TYPE_VOLCENGINE: "https://ark.cn-beijing.volces.com/api/v3",
    API_TYPE_MISTRAL: "https://api.mistral.ai/v1",
    API_TYPE_PERPLEXITY: "https://api.perplexity.ai",
    API_TYPE_TOGETHER_AI: "https://api.together.xyz/v1",
    API_TYPE_FIREWORKS_AI: "https://api.fireworks.ai/inference/v1",
    API_TYPE_CEREBRAS: "https://api.cerebras.ai/v1",
    API_TYPE_SAMBANOVA: "https://api.sambanova.ai/v1",
    API_TYPE_COHERE: "https://api.cohere.com/v2",
    API_TYPE_HUGGINGFACE: "https://router.huggingface.co/v1",
}


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
    # None 表示不下发，由 provider/SDK 按模型默认决定
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
    # 思考下发契约（供应商无关声明）：本模型如何把思考档位填进请求体。
    # {"param": "reasoning_effort"|"thinking.type", "map": {...档位映射},
    #  "on": "...开关型开启值", "off": "...关闭值"}；空 dict = 走通用透传。
    # 语义见 agent.llm.reasoning.parse_thinking_spec
    thinking: Dict[str, Any] = field(default_factory=dict)
    context_window: int = 0
    # embedding 请求的目标维度（Matryoshka 可变维度模型生效）；0 = 模型默认维度
    embedding_dims: int = 0
    # embedding 单批文本数上限（供应商限制，如 text-embedding-v4 限 10）；0 = 不限制
    embedding_max_batch: int = 0
    # embedding 协议：空 = 按 base_url 自动推断（host 含 dashscope 原生域名的走原生
    # 多模态协议，其余走 OpenAI 兼容）；显式 "dashscope_native" / "openai" 强制指定
    embedding_protocol: str = ""
    # 节点本地缓存的连接池亲和：None = 按 api_type 默认（anthropic 开），
    # True/False 显式开/关（节点本地 prompt 缓存的网关需要小连接池维持 TCP 亲和）
    cache_affinity: Optional[bool] = None
    # 图片 URL 格式："" = 按 api_type 默认（ollama 扁平）；"flat" / "nested" 显式指定
    # （扁平 = image_url 直接放字符串，嵌套 = {"url": ...} 对象，部分端点只认一种）
    image_url_format: str = ""
    request_params: Dict[str, Any] = field(default_factory=dict)
    extra_body: Dict[str, Any] = field(default_factory=dict)
    extra_params: Dict[str, Any] = field(default_factory=dict)
    # 自定义请求头（最后应用到 HTTP 请求，可覆盖鉴权头等任意头；
    # 对接需要特殊 header 的网关/中转站时使用）
    extra_headers: Dict[str, str] = field(default_factory=dict)
    chat_protocol: str = ChatProtocol.CHAT_COMPLETIONS.value
    # 供应商内置工具声明（如百炼 web_search/code_interpreter）：服务端执行、
    # 客户端不收到 tool_call；与本地同名 function 工具冲突时内置优先。
    # 每项为工具名字符串或 {"type": ..., ...} dict（web_search 可带 search_options）。
    # chat_completions 路径：web_search 转译为 enable_search（chat 端点 tools 仅收
    # function），其余类型跳过；responses 路径：以 {"type": ...} 声明透传进 tools
    builtin_tools: List[Any] = field(default_factory=list)
    # 媒体协议适配器名（image/speech/video/music 共用注册表），空表示按 host 自动匹配。
    media_protocol: str = ""
    # 启用开关：禁用后模型配置保留但不参与任何自动选择/回退/默认（模型激活）
    enabled: bool = True

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
        if not isinstance(self.extra_headers, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in self.extra_headers.items()
        ):
            raise ValueError("extra_headers 必须是字符串键值对对象")
        if not isinstance(self.builtin_tools, list) or not all(
            (isinstance(item, str) and item.strip())
            or (isinstance(item, dict) and isinstance(item.get("type"), str) and item["type"].strip())
            for item in self.builtin_tools
        ):
            raise ValueError("builtin_tools 每项必须是工具名字符串或含 type 的对象")
        normalized_effort = normalize_effort(self.reasoning_effort)
        if self.reasoning_effort and not normalized_effort:
            log(
                f"模型 [{self.name}] 配置了无效的 reasoning_effort="
                f"{self.reasoning_effort!r}，已重置为跟随全局",
                "WARNING", tag="模型",
            )
        self.reasoning_effort = normalized_effort
        if not isinstance(self.thinking, dict):
            raise ValueError("thinking 必须是对象")
        if self.thinking and parse_thinking_spec(self.thinking) is None:
            log(
                f"模型 [{self.name}] 的 thinking 契约无效（param 需为 "
                f"reasoning_effort / thinking.type），已忽略并走通用透传",
                "WARNING", tag="模型",
            )
            self.thinking = {}
        # 契约声明了 adaptive 原生值时，向 litellm 声明该端点支持 adaptive——
        # 否则 litellm 会把 adaptive 翻译成 Anthropic legacy 的 budget_tokens
        # （配置驱动：读契约值触发，不看模型名）
        if self.thinking and self._thinking_uses_adaptive():
            self._declare_adaptive_thinking()
        collisions = _RESERVED_REQUEST_PARAMS.intersection(self.request_params)
        if collisions:
            raise ValueError(f"request_params 不允许覆盖保留参数: {sorted(collisions)}")

    def _thinking_uses_adaptive(self) -> bool:
        """思考契约的任一原生值是否声明了 adaptive。"""
        values = [self.thinking.get("on"), self.thinking.get("off")]
        mapping = self.thinking.get("map")
        if isinstance(mapping, dict):
            values.extend(mapping.values())
        return any(v == "adaptive" for v in values)

    def _declare_adaptive_thinking(self) -> None:
        """向 litellm 声明本模型端点支持 adaptive thinking（防其翻译为 budget_tokens）。"""
        try:
            import litellm

            litellm.register_model({self.model: {"supports_adaptive_thinking": True}})
        except Exception as exc:
            log(f"声明 adaptive thinking 失败（不影响主流程）: {exc}", "DEBUG", tag="模型")

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
        """扁平 image_url 格式判定：显式配置优先，缺省按 api_type（ollama 扁平）。"""
        fmt = (self.image_url_format or "").strip().lower()
        if fmt:
            return fmt == "flat"
        return self.api_type == API_TYPE_OLLAMA

    @property
    def supports_base64_vision(self) -> bool:
        return self.vision_format in ("base64", "both")

    @property
    def supports_url_vision(self) -> bool:
        return self.vision_format in ("url", "both")

    def has_type(self, mt: ModelType) -> bool:
        return mt.value in self.model_types

    def normalized_builtin_tools(self) -> List[Dict[str, Any]]:
        """归一化内置工具声明：字符串转 {"type": name}，dict 浅拷贝返回。

        copy-on-write：返回的副本供 wire 层自由加工（如注入缓存断点），
        共享配置对象永不被改写。
        """
        result: List[Dict[str, Any]] = []
        for item in self.builtin_tools:
            if isinstance(item, str):
                result.append({"type": item.strip()})
            else:
                result.append(dict(item))
        return result

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
            "builtin_tools": self.builtin_tools,
            "media_protocol": self.media_protocol,
            "enabled": self.enabled,
        }
        if self.thinking:
            d["thinking"] = self.thinking
        if self.extra_params:
            d["extra_params"] = self.extra_params
        if self.extra_headers:
            d["extra_headers"] = self.extra_headers
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
        if self.builtin_tools:
            d["builtin_tools"] = self.builtin_tools
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
        if self.embedding_protocol:
            d["embedding_protocol"] = self.embedding_protocol
        if self.cache_affinity is not None:
            d["cache_affinity"] = self.cache_affinity
        if self.image_url_format:
            d["image_url_format"] = self.image_url_format
        if self.frequency_penalty:
            d["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty:
            d["presence_penalty"] = self.presence_penalty
        if self.timeout != DEFAULT_TIMEOUT:
            d["timeout"] = self.timeout
        if self.extra_params:
            d["extra_params"] = self.extra_params
        if self.extra_headers:
            d["extra_headers"] = self.extra_headers
        if self.reasoning_effort:
            d["reasoning_effort"] = self.reasoning_effort
        if self.thinking:
            d["thinking"] = self.thinking
        if not self.enabled:
            d["enabled"] = False
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
