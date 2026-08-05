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
    clamp_effort,
    downgrade_effort,
    from_litellm_effort,
    is_effort_rejection,
    normalize_effort,
    provider_specific_effort,
    to_litellm_effort,
)
from agent.llm.types import (
    ChatResult,
    ChatStreamDelta,
    ImageContent,
    TextCompletionResult,
    VideoContent,
)
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
        # DashScope 多模态向量专用 HTTP 客户端（懒创建，随 close 关闭）
        self._embed_http_client: Optional[httpx.AsyncClient] = None
        # 从端点 400 报错中学习到的 max_tokens 实际上限（本次运行内有效）
        self._learned_output_cap: Optional[int] = None
        # 从端点 400 报错中学习到的「不支持强制 tool_choice」（本次运行内有效）
        self._learned_no_forced_tool_choice: bool = False
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

    # 已知 Anthropic 兼容端点模型家族的输出上限（模型名小写子串匹配）。
    # 参考 hermes-agent anthropic_adapter 的内置表；端点实际限制更小时，
    # 由 _start_completion 的报错自适应钳制兜底，无需手动维护完整表。
    _ANTHROPIC_OUTPUT_LIMITS = {
        "minimax": 131072,
    }
    # 未知模型的默认输出预算：激进取值（新模型输出能力通常只增不减），
    # 超出端点限制时会从报错中解析真实上限并缓存，后续请求自动钳制。
    _ANTHROPIC_DEFAULT_MAX_TOKENS = 65536

    def _infer_anthropic_max_tokens(self) -> int:
        """推断 Anthropic 输出预算。

        优先级：端点报错学习到的实际上限 > litellm 模型信息 max_output_tokens
        > 模型名前缀表 > 激进默认值。不能用 litellm 的 max_tokens 键——
        它是上下文窗口（自定义模型注册时即按 context_window 写入），
        当作输出预算会超出端点限制导致 400。
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
        name = self.config.model.lower()
        for key, cap in self._ANTHROPIC_OUTPUT_LIMITS.items():
            if key in name:
                return cap
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

        三类可学习报错：强制 tool_choice 被拒（降级 auto）、max_tokens
        超限（解析端点上限并钳制）、思考等级被拒（沿降级阶梯逐档下降，
        到底后丢弃参数）。学习结果本次运行内缓存，同模型后续请求直接
        规避，实现新模型零配置自适应（参考 hermes-agent
        conversation_loop 的 available_tokens 报错解析重试）。
        每类修复各自收敛（auto/上限/阶梯单调下降），循环不会无限重试。
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
                if self._downgrade_effort_on_rejection(exc, kwargs):
                    continue
                raise

    def _downgrade_effort_on_rejection(self, exc: Exception, kwargs: Dict[str, Any]) -> bool:
        """端点 400 拒绝思考参数时沿降级阶梯降一档；最后一档丢弃参数。

        clamp_effort 静态钳制之外的运行时兜底：litellm 模型能力表与子串
        规则无法覆盖所有未知/新模型，降级保证思考参数不会导致整轮失败。
        返回 True 表示已调整 kwargs，调用方应重试。
        """
        current = kwargs.get("reasoning_effort")
        if not current or not is_effort_rejection(exc):
            return False
        effort = from_litellm_effort(str(current))
        nxt = downgrade_effort(effort)
        if nxt is not None:
            kwargs["reasoning_effort"] = to_litellm_effort(nxt)
            log(
                f"端点拒绝 reasoning_effort={current}，已降级为 "
                f"{kwargs['reasoning_effort']} 重试 (model={self.config.model})",
                "WARNING", tag="LLM",
            )
        else:
            kwargs.pop("reasoning_effort", None)
            if self.config.api_type == API_TYPE_ANTHROPIC:
                # 撤销 thinking 强制的 temperature=1，恢复模型配置值
                if self.config.temperature is not None:
                    kwargs["temperature"] = self.config.temperature
                else:
                    kwargs.pop("temperature", None)
            log(
                f"端点拒绝 reasoning_effort={current}，已丢弃该参数重试 "
                f"(model={self.config.model})",
                "WARNING", tag="LLM",
            )
        return True

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

        self._merge_request_params(kwargs, _RESERVED_REQUEST_PARAMS)

        extra = dict(self.config.extra_params)
        extra.update(self.config.extra_body)
        if self.config.supports_reasoning and self.config.api_type != API_TYPE_ANTHROPIC:
            extra.setdefault("reasoning_split", True)
        if extra:
            kwargs["extra_body"] = extra

        if effort:
            kwargs = self._apply_provider_specific_payload(effort, kwargs)

        return kwargs

    def _apply_provider_specific_payload(
            self, effort: str, kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """MiniMax / Kimi 等 anthropic 兼容通道的供应商专项 payload 转换。

        这些供应商不识别 litellm 的 reasoning_effort kwarg，必须把档位
        翻译到对应 API 字段后由 litellm 透传：
          MiniMax (M3 / M2.x)  → extra_body.thinking = {type: adaptive|disabled}
          Kimi K3                → kwargs["reasoning_effort"] = low/high/max
          Kimi K2.7-code         → extra_body.thinking = {type: enabled}
          Kimi K2.5/K2.6         → extra_body.thinking = {type: enabled|disabled}

        非供应商专项模型走通用 litellm reasoning_effort 路径（保持原行为）。
        native=None 表示供应商明确不支持该档位（如下发 K3 disabled / M2.x disabled），
        静默不下发任何思考参数（避免端点 400）。
        """
        from agent.llm.reasoning import _is_provider_specific
        is_provider_specific = _is_provider_specific(self.config.model.lower()) is not None
        native = provider_specific_effort(effort, self.config.model) if is_provider_specific else None

        if not is_provider_specific:
            # 通用路径：把规范档翻译为 litellm 接受的 kwarg
            kwargs["reasoning_effort"] = to_litellm_effort(effort)
            if self.config.api_type == API_TYPE_ANTHROPIC and effort != "off":
                kwargs["temperature"] = 1
            return kwargs

        if native is None:
            # 供应商明确不支持该档位，静默丢弃
            return kwargs

        # 供应商专项路径：K3 用顶层 reasoning_effort，其他用 extra_body.thinking
        if self._is_kimi_k3_path():
            kwargs["reasoning_effort"] = native
        else:
            existing = dict(kwargs.get("extra_body") or {})
            existing["thinking"] = {"type": native}
            kwargs["extra_body"] = existing
            if self.config.api_type == API_TYPE_ANTHROPIC and effort != "off":
                kwargs["temperature"] = 1
        return kwargs

    def _is_kimi_k3_path(self) -> bool:
        """判断当前模型是否走 Kimi K3 顶层 reasoning_effort 路径。

        K3 模型特征：原生档为 low/high/max（与 K2.x 的 enabled/disabled 区分）。
        """
        from agent.llm.reasoning import _KIMI_K3_BARE_TOKEN, _matches_bare_token
        model = self.config.model
        ml = model.lower()
        if any(s in ml for s in ("kimi-k3", "kimi_k3", "kimi.k3")):
            return True
        if _matches_bare_token(ml, _KIMI_K3_BARE_TOKEN):
            return True
        return False

    def _resolve_effort(self, params: Dict[str, Any]) -> Optional[str]:
        """解析本次调用的规范思考等级，并弹出 params 中的 reasoning_effort。

        优先级：调用方 options > 每模型专属配置（config.reasoning_effort）；
        全局等级由上层（Mind/任务/心跳）注入 options，不在此处理。
        下发前经 clamp_effort 按供应商/模型静态钳制，确保参数合法。
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
        if not self._supports_effort():
            # off（显式关闭）对不支持思考的端点本就无需下发，静默丢弃
            if effort != "off":
                self._warn_effort_dropped(effort)
            return None
        return clamp_effort(effort, self.config.model, self.config.api_type)

    def _warn_effort_dropped(self, effort: str) -> None:
        """effort 不支持时降级为告警 + 丢弃（每客户端首次）。

        reasoning_effort 多为全局配置（Mind/心跳/任务），逐模型注入会命中
        不支持推理的端点；直接 raise 会让该模型每轮必败、完全不可用，
        告警保留可追溯性，丢弃保证调用可用。
        """
        if getattr(self, "_effort_drop_warned", False):
            return
        self._effort_drop_warned = True
        log(
            f"端点不支持 reasoning_effort={effort}，已忽略该参数 "
            f"(model={self.config.model}, api_type={self.config.api_type})",
            "WARNING", tag="LLM",
        )

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

    def _supports_effort(self) -> bool:
        """检查当前模型是否支持 reasoning_effort 参数。"""
        if self.config.supports_reasoning:
            return True
        try:
            return bool(litellm.supports_reasoning(self.config.litellm_model))
        except Exception:
            return False

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
            "tools": convert_chat_tools(tools),
            # 与 chat_completions 路径一致：端点不接受强制 tool_choice 时降级
            "tool_choice": self._resolve_tool_choice(tool_choice),
            "temperature": params.get("temperature"),
            "top_p": params.get("top_p"),
            "max_output_tokens": max_output_tokens,
        }
        if effort:
            create_kwargs = self._apply_provider_specific_responses(effort, create_kwargs)
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
        result = await self._responses_create_with_effort_fallback(create_kwargs)
        return result.to_chat_result()

    def _apply_provider_specific_responses(
            self, effort: str, create_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Responses 协议路径的供应商专项思考参数转换（与 _apply_provider_specific_payload 平行）。

        Responses 通道用 create_kwargs["extra"]["reasoning"]["effort"] 嵌套结构。
        非供应商专项模型保持原 Responses 嵌套写法。
        """
        from agent.llm.reasoning import _is_provider_specific
        if _is_provider_specific(self.config.model.lower()) is None:
            create_kwargs["extra"] = {
                "reasoning": {"effort": to_litellm_effort(effort)}
            }
            return create_kwargs
        # 供应商专项模型在 Responses 通道下：用 extra_body.thinking 透传
        # （litellm Responses 桥接层会把 extra_body 合并到请求中）
        native = provider_specific_effort(effort, self.config.model)
        if native is None:
            # 供应商明确不支持该档位（如下发 K3 disabled）→ 静默丢弃
            return create_kwargs
        existing = dict(create_kwargs.get("extra") or {})
        existing["thinking"] = {"type": native}
        create_kwargs["extra"] = existing
        return create_kwargs

    async def _responses_create_with_effort_fallback(
            self, create_kwargs: Dict[str, Any],
    ) -> Any:
        """Responses 通道的思考等级降级重试（与 _start_completion 同一兜底逻辑）。"""
        while True:
            try:
                return await self.responses_create(**create_kwargs)
            except Exception as exc:
                extra = create_kwargs.get("extra") or {}
                reasoning = extra.get("reasoning") or {}
                current = reasoning.get("effort")
                if not current or not is_effort_rejection(exc):
                    raise
                nxt = downgrade_effort(from_litellm_effort(str(current)))
                if nxt is not None:
                    create_kwargs["extra"] = {
                        "reasoning": {"effort": to_litellm_effort(nxt)}
                    }
                    log(
                        f"端点拒绝 reasoning.effort={current}，已降级为 "
                        f"{to_litellm_effort(nxt)} 重试 (model={self.config.model})",
                        "WARNING", tag="LLM",
                    )
                else:
                    create_kwargs.pop("extra", None)
                    log(
                        f"端点拒绝 reasoning.effort={current}，已丢弃该参数重试 "
                        f"(model={self.config.model})",
                        "WARNING", tag="LLM",
                    )

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
                    async for item in self._iter_stream(stream, reasoning_buf, tc_bufs):
                        reasoning_buf = item[1]
                        if item[0].finish_reason:
                            last_finish = item[0].finish_reason
                        yield item[0]
            else:
                stream = await self._start_completion(kwargs)
                async for item in self._iter_stream(stream, reasoning_buf, tc_bufs):
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
    # 流式/响应解析（实现位于 agent.llm.response_parsing，绑定保持
    # LLMClient._iter_stream / _parse_response / _parse_tool_calls 等访问兼容）
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
        result = await self.chat([{"role": "user", "content": content}], options={"max_tokens": 1024})
        text = (result.content or "").strip()
        if not text:
            # 空结果视为调用失败，让上层回退到下一个视觉模型
            raise RuntimeError("视觉模型返回空结果")
        return text

    async def _describe_video_anthropic(self, video: VideoContent, prompt: str) -> str:
        """Anthropic Messages 扩展 video block 直连请求。"""
        self._ensure_configured()
        url = self.config.base_url.rstrip("/") + "/v1/messages"
        payload: Dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": [
                video.to_anthropic_block(),
                {"type": "text", "text": prompt},
            ]}],
        }
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
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
        """是否为 DashScope 原生端点（非 OpenAI 兼容模式）。"""
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
    # Token 计数与模型信息（实现位于 agent.llm.model_info，绑定保持
    # LLMClient.count_tokens / get_model_info 等访问兼容）
    # ------------------------------------------------------------------

    count_tokens = staticmethod(_mi.count_tokens)
    count_text_tokens = staticmethod(_mi.count_text_tokens)
    get_max_tokens = staticmethod(_mi.get_max_tokens)
    get_model_info = staticmethod(_mi.get_model_info)
    get_model_cost = staticmethod(_mi.get_model_cost)

    # ------------------------------------------------------------------
    # 能力探测（实现位于 agent.llm.probe，绑定保持
    # LLMClient.probe_capabilities / _make_test_png / _probe_* 访问兼容）
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
