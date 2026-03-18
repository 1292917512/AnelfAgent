"""频道系统平台无关消息模型。

所有频道将平台原始消息转换为此处定义的统一模型，
核心层仅与这些模型交互，从而实现平台解耦。
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ======================================================================
# 枚举
# ======================================================================


class ChannelType(str, Enum):
    """频道/会话类型。"""

    PRIVATE = "private"
    GROUP = "group"


class SegmentType(str, Enum):
    """消息段类型。"""

    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    AUDIO = "audio"
    VIDEO = "video"
    AT = "at"
    FILE = "file"
    LOCATION = "location"
    FORWARD = "forward"
    JSON_CARD = "json_card"


# ======================================================================
# 消息段
# ======================================================================


class MessageSegment(BaseModel):
    """接收到的消息段。"""

    type: SegmentType = SegmentType.TEXT
    content: str = ""
    url: str = ""
    file_path: str = ""
    at_user_id: str = ""
    mime_type: str = ""
    duration: float = 0
    file_name: str = ""


class SendSegment(BaseModel):
    """待发送的消息段。"""

    type: SegmentType = SegmentType.TEXT
    content: str = ""
    file_path: str = ""
    at_user_id: str = ""
    at_nickname: str = ""


# ======================================================================
# 平台无关实体
# ======================================================================


class AdapterUser(BaseModel):
    """平台用户。"""

    platform: str
    user_id: str
    user_name: str = ""
    avatar: str = ""


class AdapterChannel(BaseModel):
    """频道 / 会话。"""

    channel_id: str
    channel_type: ChannelType = ChannelType.PRIVATE
    channel_name: str = ""


# ======================================================================
# 收发消息
# ======================================================================


class AdapterMessage(BaseModel):
    """适配器接收到的消息（平台无关）。"""

    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    sender: AdapterUser
    channel: AdapterChannel
    content: str = ""
    segments: List[MessageSegment] = Field(default_factory=list)
    is_to_me: bool = False
    trigger_mind: bool = True
    timestamp: float = Field(default_factory=time.time)
    reply_to_id: str = ""
    reply_content: str = ""


class AdapterSendRequest(BaseModel):
    """适配器发送请求（平台无关）。"""

    adapter_key: str
    channel: AdapterChannel
    content: str = ""
    segments: List[SendSegment] = Field(default_factory=list)
    reply_to: Optional[str] = None
    parse_mode: Optional[str] = None
    media_urls: Optional[List[str]] = None
    buttons: Optional[List[List[dict]]] = None
    thread_id: Optional[int] = None
