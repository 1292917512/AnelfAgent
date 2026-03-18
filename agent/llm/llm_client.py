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
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Dict, List, Optional

# 必须在 import litellm 之前设置，阻止启动时拉取远端模型价格表
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import httpx
import litellm

from agent.llm.types import (
    ChatResult, ChatStreamDelta, ImageContent, TextCompletionResult, ToolCall, UsageInfo,
)
from core.entity import BaseEntity, EntityType
from core.log import debug, info

litellm.suppress_debug_info = True
litellm.drop_params = True
litellm.local_model_cost_map = True


_PROXY_ENV_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")


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
    max_tokens: int = 4096
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    timeout: float = 120.0
    proxy_url: str = ""
    supports_vision: bool = False
    supports_tools: bool = True
    vision_format: str = "base64"
    model_types: List[str] = field(default_factory=lambda: ["chat"])
    provider_id: str = ""
    supports_reasoning: bool = False
    extra_params: Dict[str, Any] = field(default_factory=dict)

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
        return f"{prefix}/{self.model}"

    @property
    def litellm_embed_model(self) -> str:
        """计算 litellm embedding 模型标识符（Ollama 使用 ollama/ 前缀）。"""
        prefix = _LITELLM_PREFIX_MAP.get(self.api_type, "openai")
        if prefix == "ollama_chat":
            prefix = "ollama"
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
            "vision_format": self.vision_format,
            "model_types": self.model_types,
            "provider_id": self.provider_id,
            "supports_reasoning": self.supports_reasoning,
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
            "vision_format": self.vision_format,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "frequency_penalty": self.frequency_penalty,
            "presence_penalty": self.presence_penalty,
            "timeout": self.timeout,
            "supports_reasoning": self.supports_reasoning,
        }
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
        if self.config.frequency_penalty:
            params["frequency_penalty"] = self.config.frequency_penalty
        if self.config.presence_penalty:
            params["presence_penalty"] = self.config.presence_penalty
        if options:
            params.update(options)
        return params

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
        params = self._gen_params(options)
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
            kwargs["tool_choice"] = tool_choice
        if stream:
            kwargs["stream"] = True

        proxy_url = self.config.effective_proxy
        if proxy_url:
            if self.config.api_type == API_TYPE_ANTHROPIC:
                for k in _PROXY_ENV_KEYS:
                    os.environ[k] = proxy_url
            else:
                proxy_client = self._get_proxy_client()
                if proxy_client:
                    kwargs["http_client"] = proxy_client
        elif self.config.api_type == API_TYPE_ANTHROPIC:
            for k in _PROXY_ENV_KEYS:
                os.environ.pop(k, None)

        extra = dict(self.config.extra_params)
        if self.config.supports_reasoning:
            extra.setdefault("reasoning_split", True)
        if extra:
            kwargs["extra_body"] = extra

        effort = params.pop("reasoning_effort", None) or ""
        if effort and self._supports_effort():
            kwargs["reasoning_effort"] = effort
            if self.config.api_type == API_TYPE_ANTHROPIC:
                kwargs["temperature"] = 1

        return kwargs

    _EFFORT_PROVIDERS = frozenset({"anthropic", "bedrock", "vertex_ai"})

    def _supports_effort(self) -> bool:
        """检查当前模型是否支持 reasoning_effort 参数。"""
        return self.config.api_type in self._EFFORT_PROVIDERS

    def _adapt_messages(self, messages: list[dict]) -> list[dict]:
        """合并头部连续 system 消息为一条，非头部 system 转 user。"""
        head_systems: list[str] = []
        rest_start = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                head_systems.append(msg.get("content", ""))
                rest_start = i + 1
            else:
                break

        adapted: list[dict] = []
        if head_systems:
            merged = "\n\n".join(s for s in head_systems if s)
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

    async def chat(
            self,
            messages: list[dict],
            *,
            options: Optional[dict] = None,
            tools: Optional[list[dict]] = None,
            tool_choice: Optional[Any] = None,
    ) -> ChatResult:
        """非流式聊天补全（通过 litellm 统一路由）。"""
        kwargs = self._build_kwargs(messages, options, tools, tool_choice)
        debug(
            f"LLM chat: {self.config.litellm_model}, msgs={len(kwargs['messages'])}",
            tag="模型",
        )
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
        stream = await litellm.acompletion(**kwargs)
        reasoning_buf = ""
        tc_bufs: Dict[int, Dict[str, str]] = {}

        async for chunk in stream:  # type: ignore[union-attr]
            if not chunk.choices:
                stream_usage = getattr(chunk, "usage", None)
                if stream_usage:
                    yield ChatStreamDelta(usage=UsageInfo(
                        prompt_tokens=getattr(stream_usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(stream_usage, "completion_tokens", 0) or 0,
                        total_tokens=getattr(stream_usage, "total_tokens", 0) or 0,
                    ))
                continue
            delta = chunk.choices[0].delta
            content = delta.content or ""
            finish = chunk.choices[0].finish_reason or ""

            reasoning = ""
            rd = getattr(delta, "reasoning_details", None)
            if rd:
                for detail in rd:
                    text = detail.get("text", "") if isinstance(detail, dict) else getattr(detail, "text", "")
                    if text and len(text) > len(reasoning_buf):
                        reasoning = text[len(reasoning_buf):]
                        reasoning_buf = text

            dtc = getattr(delta, "tool_calls", None)
            if dtc:
                for tc_chunk in dtc:
                    idx = tc_chunk.index if hasattr(tc_chunk, "index") else 0
                    buf = tc_bufs.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if hasattr(tc_chunk, "id") and tc_chunk.id:
                        buf["id"] = tc_chunk.id
                    func = getattr(tc_chunk, "function", None)
                    if func:
                        if getattr(func, "name", None):
                            buf["name"] = func.name
                        if getattr(func, "arguments", None):
                            buf["arguments"] += func.arguments

            completed_tools: list[ToolCall] = []
            if finish and tc_bufs:
                for _, buf in sorted(tc_bufs.items()):
                    if buf["name"]:
                        completed_tools.append(ToolCall(
                            id=buf["id"] or f"tc_{len(completed_tools)}",
                            name=buf["name"],
                            arguments=buf["arguments"],
                        ))
                tc_bufs.clear()

            stream_usage: Optional[UsageInfo] = None
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage and getattr(chunk_usage, "total_tokens", 0):
                stream_usage = UsageInfo(
                    prompt_tokens=getattr(chunk_usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(chunk_usage, "completion_tokens", 0) or 0,
                    total_tokens=getattr(chunk_usage, "total_tokens", 0) or 0,
                )

            yield ChatStreamDelta(
                content=content,
                tool_calls=completed_tools,
                finish_reason=finish,
                reasoning_content=reasoning,
                usage=stream_usage,
            )

    # ------------------------------------------------------------------
    # 响应解析
    # ------------------------------------------------------------------

    def _parse_response(self, resp: Any) -> ChatResult:
        """将 litellm 统一响应解析为 ChatResult（含 usage）。"""
        choice = resp.choices[0]
        msg = choice.message
        raw_dict: Optional[dict] = resp.model_dump() if hasattr(resp, "model_dump") else None

        usage = self._extract_usage(resp)

        return ChatResult(
            content=msg.content or "",
            tool_calls=self._parse_tool_calls(msg.tool_calls),
            finish_reason=choice.finish_reason or "",
            reasoning_content=self._extract_reasoning(msg, raw_dict),
            raw=raw_dict,
            usage=usage,
            model=getattr(resp, "model", "") or "",
        )

    @staticmethod
    def _extract_usage(resp: Any) -> Optional[UsageInfo]:
        """从 litellm 响应中提取 token 用量。"""
        usage = getattr(resp, "usage", None)
        if not usage:
            return None
        return UsageInfo(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
        )

    @staticmethod
    def _extract_reasoning(msg: Any, raw_response: Optional[dict] = None) -> str:
        """从响应中提取推理内容。

        支持两种来源：
        1. reasoning_details 字段（MiniMax reasoning_split 模式）
        2. <think> 标签（DeepSeek 等模型）
        """
        details = getattr(msg, "reasoning_details", None)
        if not details and raw_response:
            choices = raw_response.get("choices", [])
            if choices:
                details = choices[0].get("message", {}).get("reasoning_details")

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
            func = tc.function
            args_str = func.arguments if isinstance(func.arguments, str) else json.dumps(
                func.arguments, ensure_ascii=False,
            )
            result.append(ToolCall(
                id=tc.id or f"tc_{i}",
                name=func.name or "",
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
        return result.content

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本嵌入（通过 litellm 统一路由）。"""
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

        resp = await litellm.atext_completion(**kwargs)
        choice = resp.choices[0]
        usage = self._extract_usage(resp)
        raw_dict: Optional[dict] = resp.model_dump() if hasattr(resp, "model_dump") else None

        return TextCompletionResult(
            text=choice.text or "",
            finish_reason=choice.finish_reason or "",
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
    def _make_test_png(size: int = 16) -> bytes:
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
        }

        result: Dict[str, Any] = {
            "supports_tools": False,
            "tools_detail": "",
            "supports_vision": False,
            "vision_detail": "",
        }

        result.update(await LLMClient._probe_tools(litellm_model, probe_kw))
        result.update(await LLMClient._probe_vision(litellm_model, probe_kw, flat_url))
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
                max_tokens=128,
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

    @staticmethod
    async def _probe_vision(
            litellm_model: str, probe_kw: Dict[str, Any], flat_url: bool,
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
                max_tokens=64,
                **probe_kw,
            )
            answer = resp.choices[0].message.content or ""
            return {
                "supports_vision": True,
                "vision_detail": f"模型正确处理了图片输入: \"{answer[:80]}\"",
            }
        except Exception as exc:
            status = getattr(exc, "status_code", "")
            detail = f"不支持 (HTTP {status})" if status else f"不支持: {exc}"
            return {"supports_vision": False, "vision_detail": detail}

    # ------------------------------------------------------------------
    # 客户端生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """litellm 为无状态调用，无需关闭客户端。"""

    def update_config(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            if hasattr(self.config, k):
                setattr(self.config, k, v)
        info(f"LLMClient [{self.config.name}] 配置已更新", tag="模型")

    def __repr__(self) -> str:
        return (
            f"LLMClient(name={self.config.name!r}, "
            f"model={self.config.litellm_model!r}, "
            f"base_url={self.config.base_url!r})"
        )
