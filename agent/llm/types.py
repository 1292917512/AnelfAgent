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


def _usage_int(obj: Any, name: str) -> int:
    """从对象或 dict 上安全提取 int 字段。"""
    value = obj.get(name, 0) if isinstance(obj, dict) else getattr(obj, name, 0)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def cache_tokens_from_usage(usage: Any) -> tuple[int, int]:
    """从 usage 对象/dict 提取 (cache_read, cache_creation) tokens。

    缓存命中：Anthropic 直出 cache_read_input_tokens；OpenAI 系走
    prompt_tokens_details / input_tokens_details 的 cached_tokens。
    供 Chat Completions 与 Responses 两条解析路径共用。
    """
    if not usage:
        return 0, 0
    cache_read = _usage_int(usage, "cache_read_input_tokens")
    if not cache_read:
        details = (
            usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
            if isinstance(usage, dict)
            else (
                getattr(usage, "prompt_tokens_details", None)
                or getattr(usage, "input_tokens_details", None)
            )
        )
        if details:
            cache_read = _usage_int(details, "cached_tokens")
    return cache_read, _usage_int(usage, "cache_creation_input_tokens")


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

