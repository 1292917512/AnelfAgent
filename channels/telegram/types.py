"""Telegram 适配器类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class ThreadSpec:
    """Telegram 线程/话题标识。"""

    id: Optional[int] = None
    scope: Literal["forum", "dm", "none"] = "none"


@dataclass
class ReplyTarget:
    """回复链中被引用的消息。"""

    id: Optional[str] = None
    sender: str = ""
    body: str = ""
    kind: Literal["reply", "quote"] = "reply"
    forwarded_from: Optional[str] = None


@dataclass
class MediaResult:
    """下载后的媒体文件信息。"""

    path: str = ""
    content_type: Optional[str] = None
    placeholder: str = ""
    file_name: str = ""


@dataclass
class DeliveryResult:
    """消息发送结果。"""

    delivered: bool = False
    message_ids: List[int] = field(default_factory=list)


@dataclass
class TelegramMessageContext:
    """解析后的 Telegram 入站消息上下文。"""

    chat_id: int = 0
    is_group: bool = False
    is_forum: bool = False
    thread_spec: ThreadSpec = field(default_factory=ThreadSpec)
    sender_id: str = ""
    sender_name: str = ""
    reply_target: Optional[ReplyTarget] = None
    was_mentioned: bool = False
    implicit_mention: bool = False
    raw_text: str = ""
    media: Optional[MediaResult] = None
    message_id: int = 0
    forward_origin: Optional[str] = None


InlineButton = Dict[str, Any]
InlineKeyboard = List[List[InlineButton]]
