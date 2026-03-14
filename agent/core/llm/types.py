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


MessageContent = Union[str, List[dict[str, Any]]]
"""消息 content 类型：纯文本字符串 或 OpenAI 多模态 content 数组。"""


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ChatStreamDelta:
    """流式输出的单个片段。"""
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    reasoning_content: str = ""


@dataclass(slots=True)
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    reasoning_content: str = ""
    raw: Optional[dict[str, Any]] = None

