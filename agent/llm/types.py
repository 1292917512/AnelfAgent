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
        if self.is_url:
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
        if self.is_url:
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

    @property
    def cache_hit_rate(self) -> float:
        """本轮 prompt 缓存命中率（cache_read / prompt_tokens）。"""
        if self.prompt_tokens <= 0:
            return 0.0
        return min(1.0, self.cache_read_input_tokens / self.prompt_tokens)


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


@dataclass(slots=True)
class TextCompletionResult:
    """文本补全结果（/completions 端点）。"""
    text: str = ""
    finish_reason: str = ""
    usage: Optional[UsageInfo] = None
    raw: Optional[dict[str, Any]] = None

