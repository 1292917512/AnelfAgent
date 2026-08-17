"""
LLMClient — 统一 LLM 客户端（基于 litellm）。

通过 litellm 统一调用 100+ LLM API，自动处理协议转换：
- openai:    OpenAI 兼容 API（含 MiniMax、硅基流动等）
- anthropic: Anthropic API（含 Claude）
- ollama:    Ollama 本地模型

支持深度思考/推理内容提取（reasoning_content）。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import litellm

from agent.llm import model_info as _mi
from agent.llm import probe as _probe
from agent.llm import response_parsing as _rp
from agent.llm.config import (
    _RESERVED_REQUEST_PARAMS,
    API_TYPE_ANTHROPIC,
    API_TYPE_AZURE,
    API_TYPE_BEDROCK,
    API_TYPE_CEREBRAS,
    API_TYPE_CLOUDFLARE,
    API_TYPE_COHERE,
    API_TYPE_DASHSCOPE,
    API_TYPE_DEEPSEEK,
    API_TYPE_FIREWORKS_AI,
    API_TYPE_GEMINI,
    API_TYPE_GROQ,
    API_TYPE_HUGGINGFACE,
    API_TYPE_MISTRAL,
    API_TYPE_OLLAMA,
    API_TYPE_OPENAI,
    API_TYPE_OPENROUTER,
    API_TYPE_PERPLEXITY,
    API_TYPE_SAMBANOVA,
    API_TYPE_TOGETHER_AI,
    API_TYPE_VERTEX_AI,
    API_TYPE_VOLCENGINE,
    API_TYPE_XAI,
    API_TYPES,
    DEFAULT_TIMEOUT,
    LLMClientConfig,
    ModelType,
)
from agent.llm.protocol import ChatProtocol, resolve_chat_protocol
from agent.llm.proxy import _PROXY_ENV_KEYS, _ProxyEnvLease, _ProxyHttpClient
from agent.llm.reasoning import (
    normalize_effort,
    parse_thinking_spec,
    resolve_thinking_value,
    set_nested_field,
    to_litellm_effort,
)
from agent.llm.types import (
    ChatResult,
    ChatStreamDelta,
    ImageContent,
    TextCompletionResult,
    VideoContent,
)
from agent.llm.url_utils import infer_chat_protocol, join_endpoint, split_endpoint_suffix
from core.entity import BaseEntity, EntityType
from core.log import debug, info, log

litellm.suppress_debug_info = True
litellm.drop_params = True
litellm.local_model_cost_map = True


__all__ = [
    "API_TYPE_ANTHROPIC",
    "API_TYPE_AZURE",
    "API_TYPE_BEDROCK",
    "API_TYPE_CEREBRAS",
    "API_TYPE_CLOUDFLARE",
    "API_TYPE_COHERE",
    "API_TYPE_DASHSCOPE",
    "API_TYPE_DEEPSEEK",
    "API_TYPE_FIREWORKS_AI",
    "API_TYPE_GEMINI",
    "API_TYPE_GROQ",
    "API_TYPE_HUGGINGFACE",
    "API_TYPE_MISTRAL",
    "API_TYPE_OLLAMA",
    "API_TYPE_OPENAI",
    "API_TYPE_OPENROUTER",
    "API_TYPE_PERPLEXITY",
    "API_TYPE_SAMBANOVA",
    "API_TYPE_TOGETHER_AI",
    "API_TYPE_VERTEX_AI",
    "API_TYPE_VOLCENGINE",
    "API_TYPE_XAI",
    "API_TYPES",
    "DEFAULT_TIMEOUT",
    "LLMClient",
    "LLMClientConfig",
    "LLMNotConfiguredError",
    "ModelType",
    "_PROXY_ENV_KEYS",
]


class LLMNotConfiguredError(RuntimeError):
    """未配置可调用模型时抛出的明确异常。"""


class _CacheAffinityHTTPHandler(litellm.AsyncHTTPHandler):
    """小连接池 AsyncHTTPHandler（Anthropic 兼容端点的节点级缓存亲和）。

    kimi coding 等 anthropic 兼容网关的 prompt 缓存是**节点本地**的，
    按 TCP 连接亲和路由（实测：保活复用 100% 命中；每次新连接 8/8 全失）。
    litellm 默认池（100 连接 / 20 保活）在并发下不断开新连接——每个
    新连接大概率落到冷节点，整个前缀（含稳定层）全量 miss。限制池
    大小即限制触达节点数，保活复用维持亲和；超出并发上限时排队。
    """

    def __init__(self, pool_size: int, timeout: Optional[float] = None) -> None:
        self._pool_size = max(1, pool_size)
        super().__init__(timeout=timeout)

    def create_client(
        self,
        timeout: Any,
        event_hooks: Any = None,
        ssl_verify: Any = None,
        shared_session: Any = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=self._pool_size,
                max_keepalive_connections=self._pool_size,
            ),
            timeout=timeout or DEFAULT_TIMEOUT,
            follow_redirects=True,
        )


def _cache_affinity_pool_size() -> int:
    """缓存亲和连接池大小（配置 anthropic_cache_pool_size，默认 4）。"""
    from core.config import get_config_int
    return get_config_int("anthropic_cache_pool_size", 4)


def _clean_message_surrogates(msg: dict) -> dict:
    """清洗消息中的孤代理字符（仅文本部分；图片等多模态部件不动）。

    零拷贝：未检出孤代理时原样返回原 dict 引用；
    检出时浅拷贝消息与 content 列表，绝不污染调用方的对话历史。
    """
    from core.sanitizer import clean_surrogates, has_surrogates

    content = msg.get("content")
    if isinstance(content, str):
        if has_surrogates(content):
            return {**msg, "content": clean_surrogates(content)}
        return msg
    if isinstance(content, list):
        dirty_idx = [
            i for i, part in enumerate(content)
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and has_surrogates(part["text"])
        ]
        if not dirty_idx:
            return msg
        new_content = list(content)
        for i in dirty_idx:
            part = new_content[i]
            new_content[i] = {**part, "text": clean_surrogates(part["text"])}
        return {**msg, "content": new_content}
    return msg


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
        # 缓存亲和小连接池（懒创建，随 close 关闭；见 _CacheAffinityHTTPHandler）
        self._cache_affinity_handler: Optional[_CacheAffinityHTTPHandler] = None
        # DashScope 多模态向量专用 HTTP 客户端（懒创建，随 close 关闭）
        self._embed_http_client: Optional[httpx.AsyncClient] = None
        # 从端点 400 报错中学习到的 max_tokens 实际上限（本次运行内有效）
        self._learned_output_cap: Optional[int] = None
        # 从端点 400 报错中学习到的「不支持强制 tool_choice」（本次运行内有效）
        self._learned_no_forced_tool_choice: bool = False
        # chat_completions 不支持的内置工具类型已告警标记（每客户端只告警一次）
        self._builtin_chat_warned: bool = False
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

    @property
    def _litellm_api_base(self) -> str:
        """归一化后的 api_base：剥离 base_url 尾部误带的端点路径。

        用户可能把完整请求地址（.../v1/chat/completions、.../v1/responses）
        粘贴进 base_url；litellm 会在 api_base 上自行拼接端点路径，不剥离
        会双拼成 .../chat/completions/chat/completions。
        """
        api_base, _ = split_endpoint_suffix(self.config.base_url)
        return api_base

    def _apply_extra_headers(self, kwargs: Dict[str, Any]) -> None:
        """注入自定义请求头（用户配置最后合并，可覆盖鉴权头等任意头）。

        1h 缓存 TTL 时自动携带 Anthropic extended-cache-ttl beta 头
        （官方端点缺此头会 400；见 llm/prompt_cache）。
        """
        from agent.llm.prompt_cache import anthropic_ttl_beta_headers
        headers = anthropic_ttl_beta_headers(self.config.api_type)
        if self.config.extra_headers:
            headers.update(self.config.extra_headers)
        if headers:
            kwargs["extra_headers"] = headers

    # ------------------------------------------------------------------
    # litellm 调用参数构建
    # ------------------------------------------------------------------

    def _gen_params(self, options: Optional[dict] = None) -> Dict[str, Any]:
        """合并默认生成参数与调用时覆盖。

        Anthropic 不允许 temperature 和 top_p 同时存在，只传 temperature。
        其余不支持的参数由 litellm.drop_params=True 自动处理。
        """
        params: Dict[str, Any] = {}
        if self.config.temperature is not None:
            params["temperature"] = self.config.temperature
        if self.config.top_p is not None and self.config.api_type != API_TYPE_ANTHROPIC:
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

    def _anthropic_proxy_lease(self) -> "_ProxyEnvLease | None":
        """Anthropic 专用：返回代理环境变量租约，无代理时返回 None。

        已知限制：litellm 的 anthropic 通道不接受 httpx 客户端注入
        （其 handler 仅接受 litellm 自有 AsyncHTTPHandler，且该类不支持
        proxy 参数），无法像其他 provider 一样通过 http_client 显式传代理，
        因此保留环境变量方案，经 _ProxyEnvLease 读写分离：
        同代理请求并发，不同代理请求串行。
        """
        if self.config.api_type != API_TYPE_ANTHROPIC:
            return None
        proxy = self.config.effective_proxy
        return _ProxyEnvLease(proxy) if proxy else None

    # 未知模型的默认输出预算：激进取值（新模型输出能力通常只增不减），
    # 超出端点限制时会从报错中解析真实上限并缓存，后续请求自动钳制。
    _ANTHROPIC_DEFAULT_MAX_TOKENS = 65536
    # 视频内容描述的输出预算（视频理解只需简述，固定小预算）
    _VIDEO_DESCRIBE_MAX_TOKENS = 1024

    def _infer_anthropic_max_tokens(self) -> int:
        """推断 Anthropic 输出预算。

        优先级：端点报错学习到的实际上限 > litellm 模型信息 max_output_tokens
        > 激进默认值。不能用 litellm 的 max_tokens 键——它是上下文窗口
        （自定义模型注册时即按 context_window 写入），当作输出预算会超出
        端点限制导致 400。模型需要特定上限时在配置显式设 max_tokens。
        """
        if self._learned_output_cap:
            return self._learned_output_cap
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
                f"使用默认值 {self._ANTHROPIC_DEFAULT_MAX_TOKENS}"
                "（超限时会按端点报错自动钳制，也可在模型配置中显式指定）",
                tag="模型",
            )
        return self._ANTHROPIC_DEFAULT_MAX_TOKENS

    # 端点报错中输出上限的两种常见表述：
    #   "Range of max_tokens should be [1, 131072]"      (阿里云/千问系)
    #   "does not support max tokens > 524288"           (MiniMax 系)
    _MAX_TOKENS_RANGE_RE = re.compile(r"\[\s*1\s*,\s*(\d+)\s*\]")
    _MAX_TOKENS_GT_RE = re.compile(r"max(?:imum)?[ _]tokens?\s*>\s*(\d+)", re.IGNORECASE)

    @classmethod
    def _parse_output_cap_from_error(cls, exc: Exception) -> Optional[int]:
        """从 400 报错文本中解析端点的 max_tokens 上限，解析不到返回 None。"""
        if getattr(exc, "status_code", None) != 400 and not isinstance(
            exc, litellm.BadRequestError
        ):
            return None
        message = str(exc)
        if "max_tokens" not in message and "max tokens" not in message.lower():
            return None
        m = cls._MAX_TOKENS_RANGE_RE.search(message) or cls._MAX_TOKENS_GT_RE.search(message)
        if not m:
            return None
        cap = int(m.group(1))
        return cap if cap > 0 else None

    async def _start_completion(self, kwargs: Dict[str, Any]) -> Any:
        """发起 litellm.acompletion：端点报错自适应学习后重试。

        两类可学习报错：强制 tool_choice 被拒（降级 auto）、max_tokens
        超限（解析端点上限并钳制）。学习结果本次运行内缓存，同模型后续
        请求直接规避，实现新模型零配置自适应。每类修复各自收敛
        （auto/上限单调下降），循环不会无限重试。
        """
        while True:
            try:
                return await litellm.acompletion(**kwargs)
            except Exception as exc:
                if self._learn_tool_choice_rejection(exc, kwargs):
                    kwargs["tool_choice"] = "auto"
                    continue
                new_cap = self._parse_output_cap_from_error(exc)
                current = kwargs.get("max_tokens")
                if new_cap is not None and current and current > new_cap:
                    self._learned_output_cap = new_cap
                    kwargs["max_tokens"] = new_cap
                    info(
                        f"LLMClient [{self.config.name}] 端点限制 max_tokens ≤ {new_cap}"
                        f"（原请求 {current}），已钳制并重试，本次运行内缓存",
                        tag="模型",
                    )
                    continue
                raise

    def _learn_tool_choice_rejection(self, exc: Exception, kwargs: Dict[str, Any]) -> bool:
        """从端点 400 报错学习「不支持强制 tool_choice」。

        部分端点（如阿里 anthropic 网关的 thinking 模式）拒绝 required /
        object 形式的 tool_choice，配置项 supports_forced_tool_choice 无法
        预判所有端点行为，按报错自适应并缓存，后续请求预防性降级。
        """
        tool_choice = kwargs.get("tool_choice")
        if tool_choice is None or tool_choice in ("auto", "none"):
            return False
        if getattr(exc, "status_code", None) != 400 and not isinstance(
            exc, litellm.BadRequestError
        ):
            return False
        msg = str(exc).lower()
        if "tool_choice" not in msg or "not support" not in msg:
            return False
        self._learned_no_forced_tool_choice = True
        info(
            f"LLMClient [{self.config.name}] 端点拒绝强制 tool_choice"
            f"（请求值 {tool_choice!r}），已降级为 auto 并重试，本次运行内缓存",
            tag="模型",
        )
        return True

    def _get_proxy_client(self) -> Optional[_ProxyHttpClient]:
        """按需返回当前 Provider 的代理客户端（懒初始化）。"""
        proxy = self.config.effective_proxy
        if not proxy:
            return None
        if self._proxy_client is None or self._proxy_client.is_closed:
            self._proxy_client = _ProxyHttpClient(proxy)
        return self._proxy_client

    def _get_cache_affinity_handler(self) -> Optional[_CacheAffinityHTTPHandler]:
        """节点本地缓存的亲和 handler（懒初始化；代理场景走 env lease，不接管）。

        配置 cache_affinity 显式开关优先；缺省 anthropic 线开启（kimi coding
        等网关的 prompt 缓存是节点本地的，按 TCP 连接亲和路由）。
        """
        affinity = self.config.cache_affinity
        if affinity is None:
            affinity = self.config.api_type == API_TYPE_ANTHROPIC
        if not affinity:
            return None
        if self.config.effective_proxy:
            return None
        if self._cache_affinity_handler is None:
            self._cache_affinity_handler = _CacheAffinityHTTPHandler(
                pool_size=_cache_affinity_pool_size(),
                timeout=self.config.timeout,
            )
        return self._cache_affinity_handler

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
        effort = self._resolve_effort(params)
        adapted = self._adapt_messages(messages)

        kwargs: Dict[str, Any] = {
            "model": self.config.litellm_model,
            "messages": adapted,
            "timeout": self.config.timeout,
            **params,
        }
        # 已学习到的端点输出上限：钳制一切来源（配置/会话覆盖）的 max_tokens
        if self._learned_output_cap and kwargs.get("max_tokens"):
            kwargs["max_tokens"] = min(kwargs["max_tokens"], self._learned_output_cap)
        api_base = self._litellm_api_base
        if api_base:
            kwargs["api_base"] = api_base
        if self.config.api_key:
            kwargs["api_key"] = self.config.api_key
        if tools:
            merged_tools, builtin_patch = self._merge_builtin_tools(tools)
            kwargs["tools"] = self._apply_tools_cache_breakpoint(merged_tools, adapted)
            if builtin_patch:
                # 内置工具转译产物（enable_search 等）先行播种，
                # 后续思考方言适配与用户 extra_body 依次覆盖，用户配置最高优先
                kwargs["extra_body"] = {**builtin_patch, **(kwargs.get("extra_body") or {})}
        if tool_choice is not None:
            kwargs["tool_choice"] = self._resolve_tool_choice(tool_choice)
        if stream:
            kwargs["stream"] = True

        proxy_url = self.config.effective_proxy
        if proxy_url and self.config.api_type != API_TYPE_ANTHROPIC:
            proxy_client = self._get_proxy_client()
            if proxy_client:
                kwargs["http_client"] = proxy_client
        affinity_handler = self._get_cache_affinity_handler()
        if affinity_handler is not None:
            kwargs["client"] = affinity_handler

        if effort or self.config.thinking:
            # 有 effort 或声明了 thinking 契约都应用：effort 为空时开关型契约
            # 用 on 值（恒开模型），有档位契约无档可填则跳过
            self._apply_thinking_payload(effort, kwargs)

        # 用户扩展参数最后合并：
        # body 完全构造（含思考方言适配）之后，用户 JSON 浅合并覆盖，
        # 同名字段以用户配置为准——保证高级用户永远有最高优先级逃生舱。
        extra = dict(self.config.extra_params)
        extra.update(self.config.extra_body)
        if self.config.supports_reasoning and self.config.api_type != API_TYPE_ANTHROPIC:
            # reasoning_split 为自动默认值，用户显式配置优先
            extra = {"reasoning_split": True, **extra}
        if extra:
            existing = dict(kwargs.get("extra_body") or {})
            existing.update(extra)
            kwargs["extra_body"] = existing
        self._merge_request_params(kwargs, _RESERVED_REQUEST_PARAMS)
        self._apply_extra_headers(kwargs)

        return kwargs

    def _apply_thinking_payload(self, effort: Optional[str], kwargs: Dict[str, Any]) -> None:
        """按模型 thinking 契约把档位填进请求；无契约走通用 reasoning_effort 透传。

        核心零模型特判：契约（字段路径 + 档位映射 + 关闭值）由模型配置声明，
        这里只做"读契约填值"。effort 为空时：开关型契约用 on 值（恒开模型
        默认开启思考），有档位契约无档可填则跳过。下发载体按 api_type 区分
        （litellm 行为差异）：openai 兼容通道 extra_body 由 SDK 展开进请求体
        顶层；anthropic 兼容通道直发 body 不展开 extra_body、未收录模型顶层
        字段又被能力表卡住，故填顶层字段 + allowed_openai_params 白名单放行。
        """
        spec = parse_thinking_spec(self.config.thinking)
        value: Any
        dotted: str
        if spec is None:
            if not effort:
                return  # 无契约且无档位：不下发
            # 无契约：通用 reasoning_effort 透传（off → none）
            dotted = "reasoning_effort"
            value = to_litellm_effort(effort)
        else:
            if effort:
                native = resolve_thinking_value(spec, effort)
            else:
                # effort 为空：开关型默认开启，有档位型无档可填
                native = spec.on if (spec.on and not spec.map) else None
            if native is None:
                # 契约明确不支持该档位（如无法关闭思考），不下发任何参数
                return
            dotted = spec.param
            value = native
        if self.config.api_type == API_TYPE_ANTHROPIC:
            set_nested_field(kwargs, dotted, value)
            # 白名单放行用字段根名（litellm 按 kwarg 名匹配，不认点号路径）
            root = dotted.split(".", 1)[0]
            allowed = list(kwargs.get("allowed_openai_params") or [])
            if root not in allowed:
                kwargs["allowed_openai_params"] = allowed + [root]
        else:
            extra = dict(kwargs.get("extra_body") or {})
            set_nested_field(extra, dotted, value)
            kwargs["extra_body"] = extra

    def _resolve_effort(self, params: Dict[str, Any]) -> Optional[str]:
        """解析本次调用的规范思考等级，并弹出 params 中的 reasoning_effort。

        优先级：调用方 options > 每模型专属配置（config.reasoning_effort）；
        全局等级由上层（Mind/任务/心跳）注入 options，不在此处理。
        模型不支持思考（supports_reasoning=False）时静默忽略。
        返回 None 表示本次不下发 effort；调用方显式传入非法值抛 ValueError。
        """
        raw = params.pop("reasoning_effort", None)
        if raw is not None:
            effort = normalize_effort(raw)
            if not effort and str(raw).strip():
                raise ValueError(f"无效的 reasoning_effort: {raw}")
        else:
            effort = self.config.reasoning_effort
        if not effort:
            return None
        if not self.config.supports_reasoning:
            # 非思考模型本就无需下发（off 亦同），静默忽略
            return None
        return effort

    def _ensure_configured(self) -> None:
        if not self.config.model.strip():
            raise LLMNotConfiguredError("尚未配置可用的 LLM 模型")

    def _resolve_tool_choice(self, tool_choice: Any) -> Any:
        """端点不接受强制工具选择时，将强制值降级为 auto。

        强制值包括字符串 required 与指定工具的 object 形式
        （OpenAI {"type": "function"} / Anthropic {"type": "any"|"tool"}）。
        auto / none 与 thinking 模式兼容，原样保留。
        显式配置 supports_forced_tool_choice=false 是用户已知的预期行为，
        静默降级；仅 _learned_no_forced_tool_choice（端点 400 报错学习、
        配置预判失败）触发的降级记录 WARNING（每客户端首次）：
        强制约束意外失效是内心独白类问题的重要排查线索，不应无迹可寻。
        """
        if self.config.supports_forced_tool_choice and not self._learned_no_forced_tool_choice:
            return tool_choice
        resolved = tool_choice
        if isinstance(tool_choice, str):
            resolved = tool_choice if tool_choice in ("auto", "none") else "auto"
        elif isinstance(tool_choice, dict):
            resolved = tool_choice if tool_choice.get("type") in ("auto", "none") else "auto"
        if (
            resolved != tool_choice
            and self.config.supports_forced_tool_choice
            and not getattr(self, "_tool_choice_downgrade_warned", False)
        ):
            self._tool_choice_downgrade_warned = True
            log(
                f"端点不支持强制工具选择，tool_choice 已降级为 auto "
                f"(请求值: {tool_choice!r}, model={self.config.model})",
                "WARNING", tag="LLM",
            )
        return resolved

    def get_runtime_issues(self) -> List[str]:
        """运行时学习到的端点限制（会话内自适应生效，重启失效）。

        供 list_models/get_current_model 展示，AI 发现后可通过
        update_model_config 将修复固化为持久配置，避免重启后重踩。
        """
        issues: List[str] = []
        if self._learned_no_forced_tool_choice:
            issues.append(
                "端点拒绝强制 tool_choice（已降级为 auto，"
                "可用 update_model_config 将 supports_forced_tool_choice 固化为 false）"
            )
        if self._learned_output_cap:
            issues.append(
                f"端点限制 max_tokens ≤ {self._learned_output_cap}（已钳制，"
                f"可用 update_model_config 将 max_tokens 固化为 {self._learned_output_cap}）"
            )
        return issues

    def _merge_request_params(
        self,
        kwargs: Dict[str, Any],
        reserved: "set[str] | frozenset[str]",
    ) -> None:
        collisions = reserved.intersection(self.config.request_params)
        if collisions:
            raise ValueError(f"request_params 不允许覆盖保留参数: {sorted(collisions)}")
        kwargs.update(self.config.request_params)

    def _adapt_messages(self, messages: list[dict]) -> list[dict]:
        """合并头部连续 system 消息为一条，非头部 system 转 user。

        任一头部 system 携带 cache_control（Anthropic Prompt Caching 断点）时，
        合并输出 content block 列表并逐块保留断点；否则维持字符串/块合并原行为。
        出口清洗：字符串内容过孤代理清理（lone surrogate 会让部分
        提供商直接 400，整轮作废）。仅在检出时浅拷贝，零污染原列表。
        """
        head_systems: list[dict] = []
        rest_start = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                head_systems.append(msg)
                rest_start = i + 1
            else:
                break

        adapted: list[dict] = []
        if head_systems:
            contents = [m.get("content", "") for m in head_systems]
            has_breakpoint = any(m.get("cache_control") for m in head_systems)
            if not has_breakpoint and all(isinstance(item, str) for item in contents):
                merged: Any = "\n\n".join(item for item in contents if item)
            else:
                merged_parts: list[dict[str, Any]] = []
                for msg, item in zip(head_systems, contents, strict=False):
                    cache_control = msg.get("cache_control")
                    if isinstance(item, str):
                        if item:
                            part: dict[str, Any] = {"type": "text", "text": item}
                            if cache_control:
                                part["cache_control"] = cache_control
                            merged_parts.append(part)
                    elif isinstance(item, list):
                        parts = [p for p in item if isinstance(p, dict)]
                        if cache_control and parts:
                            # 断点作用于该消息的最后一个块（覆盖其全部前缀）
                            parts[-1] = {**parts[-1], "cache_control": cache_control}
                        merged_parts.extend(parts)
                merged = merged_parts
            adapted.append({"role": "system", "content": merged})

        for msg in messages[rest_start:]:
            if msg.get("role") == "system":
                adapted.append({**msg, "role": "user"})
            else:
                adapted.append(msg)

        return [_clean_message_surrogates(m) for m in adapted]

    def _merge_builtin_tools(self, tools: list[dict]) -> tuple[list[dict], Dict[str, Any]]:
        """chat_completions 路径：按端点能力转译内置工具，返回 (wire tools, extra_body 补丁)。

        OpenAI 兼容 chat 端点的 tools 数组仅接受 function 类型，裸
        {"type": "web_search"} 声明会被 400 拒绝（'function' is required）：
        - web_search 转译为 extra_body enable_search: true（百炼兼容模式官方
          参数；dict 形态可携带 search_options 一并下发）
        - 其余内置类型（web_extractor/code_interpreter/t2i_search/i2i_search）
          在 chat 端点无对应能力，跳过并告警一次——需要时把模型
          chat_protocol 切到 responses，由 Responses tools 声明承载
        - 与生效内置工具同名的本地 function schema 被剔除（内置优先）
        无配置时原样返回 (tools, {})（同一对象）。
        """
        builtins = self.config.normalized_builtin_tools()
        if not builtins:
            return tools, {}
        patch: Dict[str, Any] = {}
        active_types: set[str] = set()
        skipped: list[str] = []
        for b in builtins:
            btype = b["type"]
            if btype == "web_search":
                patch["enable_search"] = True
                if isinstance(b.get("search_options"), dict):
                    patch["search_options"] = dict(b["search_options"])
                active_types.add(btype)
            else:
                skipped.append(btype)
        if skipped and not self._builtin_chat_warned:
            self._builtin_chat_warned = True
            log(
                f"模型 [{self.config.name}] 的内置工具 {skipped} 在 chat_completions "
                "端点不被接受（tools 仅支持 function），已跳过；"
                "如需启用请将 chat_protocol 切换为 responses",
                "WARNING", tag="模型",
            )
        merged = [
            t for t in tools
            if not (
                t.get("type") == "function"
                and isinstance(t.get("function"), dict)
                and t["function"].get("name") in active_types
            )
        ]
        return merged, patch

    def _merge_responses_builtin_tools(self, tools: list[dict]) -> list[dict]:
        """Responses 路径：内置工具以 {"type": ...} 声明追加进 tools 数组。

        Responses API 原生支持内置工具声明（百炼兼容模式同样走 tools 数组）。
        与本地 function 工具同名时内置优先——剔除本地 schema；
        内置声明追加在尾部，位置随模型配置静态固定。
        """
        builtins = self.config.normalized_builtin_tools()
        if not builtins:
            return tools
        builtin_types = {b["type"] for b in builtins}
        merged = [
            t for t in tools
            if not (
                t.get("type") == "function"
                and isinstance(t.get("function"), dict)
                and t["function"].get("name") in builtin_types
            )
        ]
        merged.extend(builtins)
        return merged

    def _apply_tools_cache_breakpoint(
            self,
            tools: list[dict],
            adapted: list[dict],
    ) -> list[dict]:
        """wire tools 数组末尾注入缓存断点（自限额：消息侧断点未满才补位）。

        仅 Anthropic 线生效。断点预算每请求 4 个（见 llm/prompt_cache）：
        工具链轮次消息侧已满 4 个时跳过——stable 层末断点的前缀本就覆盖
        tools 数组；首轮（无工具链断点）补位后，人设/目录文本变更时
        工具数组前缀仍可命中缓存。
        """
        if self.config.api_type != API_TYPE_ANTHROPIC:
            return tools
        from agent.llm.prompt_cache import (
            MAX_BREAKPOINTS,
            apply_tools_breakpoint,
            count_breakpoints,
            is_tools_breakpoint_enabled,
        )
        if not is_tools_breakpoint_enabled():
            return tools
        if count_breakpoints(adapted) >= MAX_BREAKPOINTS:
            return tools
        result = apply_tools_breakpoint(tools, api_type=self.config.api_type)
        return result if result is not None else tools

    # ------------------------------------------------------------------
    # ChatModel 协议：chat
    # ------------------------------------------------------------------

    @property
    def resolved_chat_protocol(self) -> ChatProtocol:
        """解析当前模型实际使用的对话协议。

        base_url 显式携带端点路径时（.../responses、.../chat/completions），
        URL 是端点形态的事实真相，优先于配置推断。
        """
        inferred = infer_chat_protocol(self.config.base_url)
        if inferred:
            return ChatProtocol(inferred)
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
            api_base=self._litellm_api_base,
            api_key=self.config.api_key,
            timeout=self.config.timeout,
            request_params=self.config.request_params,
            extra_body={**self.config.extra_params, **self.config.extra_body},
            extra_headers=self.config.extra_headers or None,
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
        lease = self._anthropic_proxy_lease()
        if lease:
            async with lease:
                async for event in client.stream(**kwargs):
                    yield event
        else:
            async for event in client.stream(**kwargs):
                yield event

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
        lease = self._anthropic_proxy_lease()
        if lease:
            async with lease:
                return await awaitable
        return await awaitable

    def _build_responses_kwargs(
            self,
            messages: list[dict],
            options: Optional[dict],
            tools: Optional[list[dict]],
            tool_choice: Optional[Any],
    ) -> Dict[str, Any]:
        """构建 Responses create/stream 共享的调用参数（含思考等级映射）。"""
        from agent.llm.responses.client import convert_chat_tools, messages_to_responses_input

        adapted = self._adapt_messages(messages)
        instructions, input_payload = messages_to_responses_input(adapted)
        params = self._gen_params(options)
        effort = self._resolve_effort(params)
        # 已学习到的端点输出上限：与 chat_completions 路径一致地钳制
        max_output_tokens = params.get("max_tokens")
        if max_output_tokens and self._learned_output_cap:
            max_output_tokens = min(max_output_tokens, self._learned_output_cap)
        create_kwargs: Dict[str, Any] = {
            "input": input_payload,
            "instructions": instructions,
            "tools": convert_chat_tools(
                self._merge_responses_builtin_tools(tools) if tools else tools,
            ),
            # 与 chat_completions 路径一致：端点不接受强制 tool_choice 时降级
            "tool_choice": self._resolve_tool_choice(tool_choice),
            "temperature": params.get("temperature"),
            "top_p": params.get("top_p"),
            "max_output_tokens": max_output_tokens,
        }
        if effort:
            create_kwargs["extra"] = {"reasoning": {"effort": to_litellm_effort(effort)}}
        debug(
            f"LLM chat(via responses): {self.config.litellm_model}, msgs={len(adapted)}",
            tag="模型",
        )
        return create_kwargs

    async def _chat_via_responses(
            self,
            messages: list[dict],
            *,
            options: Optional[dict] = None,
            tools: Optional[list[dict]] = None,
            tool_choice: Optional[Any] = None,
    ) -> ChatResult:
        create_kwargs = self._build_responses_kwargs(messages, options, tools, tool_choice)
        result = await self.responses_create(**create_kwargs)
        return result.to_chat_result()

    async def _chat_stream_via_responses(
            self,
            messages: list[dict],
            *,
            options: Optional[dict] = None,
            tools: Optional[list[dict]] = None,
            tool_choice: Optional[Any] = None,
    ) -> AsyncGenerator[ChatStreamDelta, None]:
        """Responses 协议的流式聊天：事件流转 ChatStreamDelta。

        文本/思考增量即时下发；工具调用与 usage 随终态事件
        （response.completed）一并输出，与 chat_completions 流式契约一致
        （工具调用仅在完整后下发，避免残缺 JSON arguments）。
        """
        from agent.llm.responses.client import parse_responses_payload

        stream_kwargs = self._build_responses_kwargs(messages, options, tools, tool_choice)
        async for event in self.responses_stream(**stream_kwargs):
            if event.type == "response.output_text.delta":
                text = str(event.data.get("delta") or "")
                if text:
                    yield ChatStreamDelta(content=text)
            elif event.type in (
                "response.reasoning_text.delta",
                "response.reasoning_summary_text.delta",
            ):
                reasoning = str(event.data.get("delta") or "")
                if reasoning:
                    yield ChatStreamDelta(reasoning_content=reasoning)
            elif event.type in ("response.completed", "response.incomplete"):
                result = parse_responses_payload(event.data.get("response") or event.data)
                chat_result = result.to_chat_result()
                yield ChatStreamDelta(
                    tool_calls=chat_result.tool_calls,
                    finish_reason=chat_result.finish_reason or "stop",
                    usage=chat_result.usage,
                )
            elif event.type in ("response.failed", "response.error", "error"):
                result = parse_responses_payload(event.data.get("response") or event.data)
                raise RuntimeError(
                    f"Responses 流式调用失败: {result.error or event.data}"
                )

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
        lease = self._anthropic_proxy_lease()
        if lease:
            async with lease:
                resp = await self._start_completion(kwargs)
        else:
            resp = await self._start_completion(kwargs)
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
        仅当 finish_reason 为 "tool_calls" 或 "stop" 时随最终 delta 输出；
        finish=="length" 或无 finish chunk 时视为输出被截断，丢弃不完整
        缓冲并记录 WARNING，避免下发出残缺 JSON arguments 的工具调用。
        usage 仅在最终 chunk（finish chunk 或无 choices 的 usage-only chunk）输出。
        chat_protocol=responses 时分发到 Responses 流式实现。
        """
        if self.resolved_chat_protocol == ChatProtocol.RESPONSES:
            async for delta in self._chat_stream_via_responses(
                messages,
                options=options,
                tools=tools,
                tool_choice=tool_choice,
            ):
                yield delta
            return
        kwargs = self._build_kwargs(messages, options, tools, tool_choice, stream=True)
        kwargs["stream_options"] = {"include_usage": True}
        stream: Any = None
        reasoning_buf = ""
        tc_bufs: Dict[int, Dict[str, str]] = {}
        last_finish = ""
        try:
            lease = self._anthropic_proxy_lease()
            if lease:
                async with lease:
                    stream = await self._start_completion(kwargs)
                    sink = _rp.install_usage_tap(stream)
                    async for item in self._iter_stream(stream, reasoning_buf, tc_bufs, sink):
                        reasoning_buf = item[1]
                        if item[0].finish_reason:
                            last_finish = item[0].finish_reason
                        yield item[0]
            else:
                stream = await self._start_completion(kwargs)
                sink = _rp.install_usage_tap(stream)
                async for item in self._iter_stream(stream, reasoning_buf, tc_bufs, sink):
                    reasoning_buf = item[1]
                    if item[0].finish_reason:
                        last_finish = item[0].finish_reason
                    yield item[0]
        finally:
            if stream is not None:
                close_fn = getattr(stream, "aclose", None)
                if close_fn:
                    await close_fn()

        if tc_bufs:
            if last_finish in ("tool_calls", "stop"):
                yield ChatStreamDelta(
                    tool_calls=self._complete_tool_buffers(tc_bufs),
                    finish_reason="tool_calls",
                )
            else:
                log(
                    f"流式响应被截断（finish_reason={last_finish or '缺失'}），"
                    f"丢弃 {len(tc_bufs)} 个不完整的 tool_call 缓冲",
                    "WARNING", tag="模型",
                )
                tc_bufs.clear()

    # ------------------------------------------------------------------
    # 流式/响应解析（实现位于 agent.llm.response_parsing，此处做访问绑定）
    # ------------------------------------------------------------------

    _iter_stream = staticmethod(_rp._iter_stream)
    _normalize_tc_index = staticmethod(_rp._normalize_tc_index)
    _complete_tool_buffers = staticmethod(_rp._complete_tool_buffers)
    _parse_response = staticmethod(_rp._parse_response)
    _extract_usage = staticmethod(_rp._extract_usage)
    _usage_from_object = staticmethod(_rp._usage_from_object)
    _extract_reasoning = staticmethod(_rp._extract_reasoning)
    _parse_tool_calls = staticmethod(_rp._parse_tool_calls)

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

    async def describe_video(
            self,
            video: VideoContent,
            prompt: str = "请简要描述这个视频的内容。",
    ) -> str:
        """视频理解：描述视频内容。

        anthropic 类端点走原生 video content block 直连（litellm 的 Anthropic
        转换层不认识 video block，会在消息校验阶段拒绝）；其余端点走 OpenAI
        兼容的 video_url content block。
        """
        if self.config.api_type == API_TYPE_ANTHROPIC:
            return await self._describe_video_anthropic(video, prompt)
        content: list[dict] = [
            {"type": "text", "text": prompt},
            video.to_openai_block(),
        ]
        result = await self.chat([{"role": "user", "content": content}],
                                 options={"max_tokens": self._VIDEO_DESCRIBE_MAX_TOKENS})
        text = (result.content or "").strip()
        if not text:
            # 空结果视为调用失败，让上层回退到下一个视觉模型
            raise RuntimeError("视觉模型返回空结果")
        return text

    async def _describe_video_anthropic(self, video: VideoContent, prompt: str) -> str:
        """Anthropic Messages 扩展 video block 直连请求。"""
        self._ensure_configured()
        url = join_endpoint(self.config.base_url, "/v1/messages")
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self._VIDEO_DESCRIBE_MAX_TOKENS,
            "messages": [{"role": "user", "content": [
                video.to_anthropic_block(),
                {"type": "text", "text": prompt},
            ]}],
        }
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
        headers.update(self.config.extra_headers)
        resp = await self._direct_http().post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"视频识别请求失败 (HTTP {resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ).strip()
        if not text:
            # 空结果视为调用失败，让上层回退到下一个视觉模型
            raise RuntimeError("视觉模型返回空结果")
        return text

    def _direct_http(self) -> httpx.AsyncClient:
        """绕过 litellm 的直连请求共享连接池（原生向量 / 视频识别复用）。"""
        if self._embed_http_client is None or self._embed_http_client.is_closed:
            self._embed_http_client = httpx.AsyncClient(
                timeout=self.config.timeout,
                proxy=self.config.effective_proxy or None,
            )
        return self._embed_http_client

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    # DashScope 原生向量端点（OpenAI 兼容模式不支持多模态系列模型）：
    # 多模态模型（supports_vision）走 multimodal-embedding，纯文本模型走 text-embedding
    _DASHSCOPE_EMBED_PATH = "/services/embeddings/multimodal-embedding/multimodal-embedding"
    _DASHSCOPE_TEXT_EMBED_PATH = "/services/embeddings/text-embedding/text-embedding"

    def _is_dashscope_native(self) -> bool:
        """是否为 DashScope 原生端点（非 OpenAI 兼容模式）。

        显式配置 embedding_protocol 优先；缺省按 base_url host 推断
        （同供应商两套协议的兼容兜底，可被显式配置覆盖）。
        """
        proto = (self.config.embedding_protocol or "").strip().lower()
        if proto:
            return proto == "dashscope_native"
        url = self.config.base_url or ""
        return "dashscope.aliyuncs.com" in url and "compatible-mode" not in url

    @property
    def supports_multimodal_embedding(self) -> bool:
        """是否支持图片等多模态向量化（当前仅 DashScope 原生多模态向量 API）。"""
        return self._is_dashscope_native() and self.config.supports_vision

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本嵌入（默认通过 litellm 统一路由；DashScope 原生端点按模型能力分流）。

        配置 embedding_max_batch 时按供应商单批上限自动拆分请求并按序拼接。
        """
        self._ensure_configured()
        max_batch = self.config.embedding_max_batch
        if max_batch > 0 and len(texts) > max_batch:
            results: list[list[float]] = []
            for i in range(0, len(texts), max_batch):
                results.extend(await self.embed(texts[i:i + max_batch]))
            return results
        if self._is_dashscope_native():
            if self.config.supports_vision:
                return await self.embed_multimodal([{"text": t} for t in texts])
            return await self._embed_dashscope_native_text(texts)
        kwargs: Dict[str, Any] = {
            "model": self.config.litellm_embed_model,
            "input": texts,
            "timeout": self.config.timeout,
            "encoding_format": "float",
        }
        if self.config.embedding_dims > 0:
            kwargs["dimensions"] = self.config.embedding_dims
        if self._litellm_api_base:
            kwargs["api_base"] = self._litellm_api_base
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
        self._apply_extra_headers(kwargs)
        proxy_client = self._get_proxy_client()
        if proxy_client and self.config.api_type != API_TYPE_ANTHROPIC:
            kwargs["http_client"] = proxy_client
        lease = self._anthropic_proxy_lease()
        if lease:
            async with lease:
                resp = await litellm.aembedding(**kwargs)
        else:
            resp = await litellm.aembedding(**kwargs)
        return [item["embedding"] for item in resp.data]

    async def embed_multimodal(self, contents: list[dict]) -> list[list[float]]:
        """DashScope 原生多模态向量。

        Args:
            contents: {"text": "..."} / {"image": "URL 或 data URL"} 列表，
                返回与 contents 按 index 对齐的向量列表。
        """
        self._ensure_configured()
        if not self._is_dashscope_native():
            raise RuntimeError("多模态向量当前仅支持 DashScope 原生端点")
        url = self.config.base_url.rstrip("/") + self._DASHSCOPE_EMBED_PATH
        payload: Dict[str, Any] = {"model": self.config.model, "input": {"contents": contents}}
        if self.config.embedding_dims > 0:
            payload["parameters"] = {"dimension": self.config.embedding_dims}
        data = await self._dashscope_embed_request(url, payload)
        embeddings = data.get("output", {}).get("embeddings", [])
        ordered = sorted(embeddings, key=lambda e: e.get("index", 0))
        return [e["embedding"] for e in ordered]

    async def _embed_dashscope_native_text(self, texts: list[str]) -> list[list[float]]:
        """DashScope 原生纯文本向量（text-embedding 端点）。"""
        url = self.config.base_url.rstrip("/") + self._DASHSCOPE_TEXT_EMBED_PATH
        payload: Dict[str, Any] = {"model": self.config.model, "input": {"texts": texts}}
        if self.config.embedding_dims > 0:
            payload["parameters"] = {"dimension": self.config.embedding_dims}
        data = await self._dashscope_embed_request(url, payload)
        embeddings = data.get("output", {}).get("embeddings", [])
        ordered = sorted(embeddings, key=lambda e: e.get("index", 0))
        return [e["embedding"] for e in ordered]

    async def _dashscope_embed_request(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """DashScope 原生向量端点请求（复用直连连接池与有效代理）。"""
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        headers.update(self.config.extra_headers)
        resp = await self._direct_http().post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

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
        if self._litellm_api_base:
            kwargs["api_base"] = self._litellm_api_base
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
        self._apply_extra_headers(kwargs)
        proxy_client = self._get_proxy_client()
        if proxy_client and self.config.api_type != API_TYPE_ANTHROPIC:
            kwargs["http_client"] = proxy_client
        lease = self._anthropic_proxy_lease()
        if lease:
            async with lease:
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
    # Token 计数与模型信息（实现位于 agent.llm.model_info，此处做访问绑定）
    # ------------------------------------------------------------------

    count_tokens = staticmethod(_mi.count_tokens)
    count_text_tokens = staticmethod(_mi.count_text_tokens)
    get_max_tokens = staticmethod(_mi.get_max_tokens)
    get_model_info = staticmethod(_mi.get_model_info)
    get_model_cost = staticmethod(_mi.get_model_cost)

    # ------------------------------------------------------------------
    # 能力探测（实现位于 agent.llm.probe，此处做访问绑定）
    # ------------------------------------------------------------------

    _BASE64_ONLY_TYPES = _probe._BASE64_ONLY_TYPES
    _URL_VISION_KNOWN_TYPES = _probe._URL_VISION_KNOWN_TYPES
    _make_test_png = staticmethod(_probe._make_test_png)
    probe_capabilities = staticmethod(_probe.probe_capabilities)
    _probe_tools = staticmethod(_probe._probe_tools)
    _probe_vision = staticmethod(_probe._probe_vision)

    # ------------------------------------------------------------------
    # 客户端生命周期
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """关闭按客户端持有的代理连接池与多模态向量客户端。"""
        client = self._proxy_client
        self._proxy_client = None
        if client is not None and not client.is_closed:
            await client.aclose()
        embed_client = self._embed_http_client
        self._embed_http_client = None
        if embed_client is not None and not embed_client.is_closed:
            await embed_client.aclose()
        affinity_handler = self._cache_affinity_handler
        self._cache_affinity_handler = None
        if affinity_handler is not None and not affinity_handler.client.is_closed:
            await affinity_handler.client.aclose()

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
        if self._embed_http_client is not None:
            # 代理/超时等连接参数可能已变化，丢弃旧客户端下次重建
            stale_embed = self._embed_http_client
            self._embed_http_client = None
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(stale_embed.aclose())
            except RuntimeError:
                pass
        info(f"LLMClient [{self.config.name}] 配置已更新", tag="模型")

    def __repr__(self) -> str:
        return (
            f"LLMClient(name={self.config.name!r}, "
            f"model={self.config.litellm_model!r}, "
            f"base_url={self.config.base_url!r})"
        )
