from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Union


@dataclass(slots=True)
class ImageContent:
    """图像内容（base64 编码数据或 URL）。"""

    data: str
    mime_type: str = "image/jpeg"
    is_url: bool = False

    def to_openai_block(self, *, flat_url: bool = False) -> dict[str, Any]:
        """转换为 image_url content block。

        Args:
            flat_url: 为 True 时使用 Ollama 兼容的扁平字符串格式；
                      为 False 时使用 OpenAI 标准嵌套 ``{"url": ...}`` 格式。
        """
        if self.is_url or self.data.startswith("data:"):
            # data URL 形态的输入原样透传，不再二次包装成
            # data:image/jpeg;base64,data:image/png;base64,... 坏块
            url = self.data
        else:
            url = f"data:{self.mime_type};base64,{self.data}"
        if flat_url:
            return {"type": "image_url", "image_url": url}
        return {"type": "image_url", "image_url": {"url": url}}


@dataclass(slots=True)
class VideoContent:
    """视频内容（base64 编码数据或 URL）。"""

    data: str
    mime_type: str = "video/mp4"
    is_url: bool = False

    def to_anthropic_block(self) -> dict[str, Any]:
        """转换为 Anthropic Messages 扩展的 video content block。"""
        if self.is_url:
            source: dict[str, Any] = {"type": "url", "url": self.data}
        else:
            source = {"type": "base64", "media_type": self.mime_type, "data": self.data}
        return {"type": "video", "source": source}

    def to_openai_block(self) -> dict[str, Any]:
        """转换为 OpenAI 兼容的 video_url content block。"""
        if self.is_url or self.data.startswith("data:"):
            url = self.data
        else:
            url = f"data:{self.mime_type};base64,{self.data}"
        return {"type": "video_url", "video_url": {"url": url}}


MessageContent = Union[str, List[dict[str, Any]]]
"""消息 content 类型：纯文本字符串 或 OpenAI 多模态 content 数组。"""


def _dig_int(obj: Any, dotted_path: str) -> int:
    """按点分路径从对象/dict 提取 int（任一层缺失或非法返回 0）。"""
    current = obj
    for part in dotted_path.split("."):
        if current is None:
            return 0
        current = current.get(part) if isinstance(current, dict) else getattr(current, part, None)
    try:
        return int(current or 0)
    except (TypeError, ValueError):
        return 0


def _usage_int(obj: Any, name: str) -> int:
    """从对象或 dict 上安全提取 int 字段。"""
    return _dig_int(obj, name)


# 缓存用量字段注册表（点分路径，按声明顺序取第一个非零值）。
# 供应商差异全部沉淀在这张表里：接入新供应商只需登记字段路径，解析逻辑零分支。
_CACHE_READ_PATHS: tuple[str, ...] = (
    "cache_read_input_tokens",              # Anthropic Messages 协议
    "prompt_cache_hit_tokens",              # DeepSeek 磁盘缓存（自动生效）
    "prompt_tokens_details.cached_tokens",  # OpenAI Chat Completions
    "input_tokens_details.cached_tokens",   # OpenAI Responses 协议
)
_CACHE_CREATION_PATHS: tuple[str, ...] = (
    "cache_creation_input_tokens",          # Anthropic Messages 协议（显式缓存写入）
)


def cache_tokens_from_usage(usage: Any) -> tuple[int, int]:
    """从 usage 对象/dict 提取 (cache_read, cache_creation) tokens。

    供 Chat Completions 与 Responses 两条解析路径共用。
    """
    if not usage:
        return 0, 0
    cache_read = next((v for p in _CACHE_READ_PATHS if (v := _dig_int(usage, p))), 0)
    cache_creation = next((v for p in _CACHE_CREATION_PATHS if (v := _dig_int(usage, p))), 0)
    return cache_read, cache_creation


def _dig_present(obj: Any, dotted_path: str) -> bool:
    """按点分路径判断字段是否存在（值为 0 也算存在，区别于 _dig_int）。"""
    current = obj
    for part in dotted_path.split("."):
        if current is None:
            return False
        current = current.get(part) if isinstance(current, dict) else getattr(current, part, None)
    return current is not None


def usage_has_cache_fields(usage: Any) -> bool:
    """usage 是否携带缓存统计字段（存在性判定，与命中值无关）。

    用于区分"端点真实未命中"（字段在、值为 0）与"端点不回报"
    （字段缺失 = 不可观测）——前者显示 0%，后者显示"不可观测"。
    """
    if not usage:
        return False
    paths = _CACHE_READ_PATHS + _CACHE_CREATION_PATHS
    return any(_dig_present(usage, p) for p in paths)


def usage_prompt_includes_cache(usage: Any) -> bool:
    """prompt_tokens 是否已含缓存 tokens（两种记账口径归一）。

    含缓存的信号（details 包装 / DeepSeek 命中字段）：
    - prompt_tokens_details.cached_tokens / input_tokens_details.cached_tokens
      存在（OpenAI 两协议，以及 litellm 变换后的 Anthropic——其
      calculate_usage 把缓存加回 prompt_tokens 并必附 details 包装）；
    - prompt_cache_hit_tokens 存在（DeepSeek 磁盘缓存，prompt 含命中）。

    仅有原生 Anthropic 字段（cache_read_input_tokens/cache_creation_input_tokens）
    而无 details 包装 = 原始 Anthropic 记账（input_tokens 不含缓存，
    流式 chunk 与 anthropic 兼容网关透传即此形态），prompt_tokens 需补回。
    """
    if not usage:
        return True
    if _dig_present(usage, "prompt_tokens_details.cached_tokens"):
        return True
    if _dig_present(usage, "input_tokens_details.cached_tokens"):
        return True
    if _dig_present(usage, "prompt_cache_hit_tokens"):
        return True
    if _dig_present(usage, "cache_read_input_tokens") or _dig_present(
        usage, "cache_creation_input_tokens"
    ):
        return False
    return True


_caliber_conflict_warned = False


def _warn_caliber_conflict_once() -> None:
    """口径冲突告警（每进程一次）：端点缓存命中量超过 prompt_tokens。"""
    global _caliber_conflict_warned
    if _caliber_conflict_warned:
        return
    _caliber_conflict_warned = True
    from core.log import log
    log(
        "LLM 用量口径冲突：缓存命中量超过 prompt_tokens，该端点实际按"
        " prompt 不含缓存记账，已按此归一（此前命中率显示为虚高）",
        "WARNING",
    )


@dataclass(slots=True)
class UsageInfo:
    """LLM 调用的 token 用量统计。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # 供应商侧 prompt 缓存命中量（Anthropic 直出；OpenAI cached_tokens 映射到此）
    cache_read_input_tokens: int = 0
    # 供应商侧缓存写入量（仅 Anthropic 等显式缓存协议提供）
    cache_creation_input_tokens: int = 0
    # 端点是否回报缓存统计字段（False = 不可观测，展示"—"而非谎报 0%；
    # 由流式旁路按原始 chunk 字段存在性动态判定，替代供应商名静态登记）
    cache_observable: bool = True
    # prompt_tokens 是否已含缓存 tokens（提取时按记账口径判定，见
    # usage_prompt_includes_cache）；False 时总输入 = prompt + read + creation
    prompt_includes_cache: bool = True

    def __post_init__(self) -> None:
        # 记账口径自洽校验：含缓存口径下 read+creation 必然 ≤ prompt；超过即
        # 端点实际按"prompt 不含缓存"记账。注意 read>prompt 也可能是上游数值
        # 尺度混淆的假象（2026-09 实证：litellm 1.100 对未收录模型用 tiktoken
        # 估算伪造流式 prompt、真实 read 经旁路补回，两者尺度不一必然冲突——
        # 已由旁路全字段优先根治，本守卫仅剩兜底意义）；冲突仍按含缓存不可能
        # 的不变量归一，不校正则命中率被 min(1.0, …) 钳成 100% 虚报。
        if (
            self.prompt_includes_cache
            and self.cache_read_input_tokens + self.cache_creation_input_tokens
            > self.prompt_tokens
        ):
            self.prompt_includes_cache = False
            _warn_caliber_conflict_once()

    @property
    def total_input_tokens(self) -> int:
        """总输入 tokens（两种记账口径归一后的分母）。"""
        if self.prompt_includes_cache:
            return self.prompt_tokens
        return (
            self.prompt_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    @property
    def cache_hit_rate(self) -> float:
        """本轮 prompt 缓存命中率（命中词 / 总输入词）。

        分母为归一后的总输入：修复原生 Anthropic 记账口径（input_tokens
        不含缓存）下 read/prompt > 1 被钳到 100% 的虚报。
        """
        total = self.total_input_tokens
        if total <= 0:
            return 0.0
        return min(1.0, self.cache_read_input_tokens / total)


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str
    raw: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def wire_raw(call_id: str, name: str, arguments: str) -> dict[str, Any]:
        """构造 OpenAI 线格式的 tool_call raw 结构。

        think_loop 用 raw 拼装 assistant 历史消息（tool_calls 字段），
        缺 id 或结构不完整会破坏与 tool 消息的配对。
        """
        return {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        }


@dataclass(slots=True)
class ChatStreamDelta:
    """流式输出的单个片段。"""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    reasoning_content: str = ""
    usage: Optional[UsageInfo] = None


@dataclass(slots=True)
class ChatResult:
    """LLM 聊天补全结果。"""
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    reasoning_content: str = ""
    raw: Optional[dict[str, Any]] = None
    usage: Optional[UsageInfo] = None
    model: str = ""
    # 首 token 时间（毫秒，流式路径填充。区分"模型排队慢"与"输出生成长"
    # 两个独立的延迟来源）。非流式为 None。
    ttft_ms: Optional[float] = None


@dataclass(slots=True)
class TextCompletionResult:
    """文本补全结果（/completions 端点）。"""
    text: str = ""
    finish_reason: str = ""
    usage: Optional[UsageInfo] = None
    raw: Optional[dict[str, Any]] = None

