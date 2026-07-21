"""频道系统平台无关消息模型。

所有频道将平台原始消息转换为此处定义的统一模型，
核心层仅与这些模型交互，从而实现平台解耦。

包含：
- 入站消息模型（AdapterMessage / MessageSegment）
- 出站消息模型（SendRequest / SendResponse / SendSegment）
- 实体信息模型（ChannelUser / ChannelInfo）
- 健康探针模型（HealthStatus）
- 命令系统模型（CommandResponse）
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

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
    mime_type: str = ""
    caption: str = ""
    parse_mode: Optional[str] = None


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


class SendRequest(BaseModel):
    """统一发送请求 — 频道适配器的唯一发送入口。

    所有发送动作（文本、图片、文件、@、回复等）都打包成 SendRequest，
    由频道适配器的 forward_message() 处理。
    """

    adapter_key: str
    channel: AdapterChannel
    segments: List[SendSegment] = Field(default_factory=list)
    reply_to: Optional[str] = None
    parse_mode: Optional[str] = None
    silent: bool = False
    thread_id: Optional[int] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class SendResponse(BaseModel):
    """统一发送响应。"""

    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    message_ids: List[str] = Field(default_factory=list)


# ======================================================================
# 实体信息（自身 / 用户 / 频道）
# ======================================================================


class ChannelUserRole(str, Enum):
    """用户在频道中的角色。"""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    GUEST = "guest"


class ChannelUser(BaseModel):
    """平台用户信息（统一返回模型）。"""

    platform: str
    user_id: str
    user_name: str = ""
    avatar: str = ""
    role: ChannelUserRole = ChannelUserRole.MEMBER
    is_bot: bool = False
    extra: Dict[str, Any] = Field(default_factory=dict)


class ChannelInfo(BaseModel):
    """频道 / 会话信息（统一返回模型）。"""

    channel_id: str
    channel_name: str = ""
    channel_type: ChannelType = ChannelType.PRIVATE
    member_count: Optional[int] = None
    avatar: str = ""
    description: str = ""
    extra: Dict[str, Any] = Field(default_factory=dict)


# ======================================================================
# 健康探针
# ======================================================================


class HealthStatus(BaseModel):
    """频道健康状态。"""

    healthy: bool
    detail: str = ""
    last_error: Optional[str] = None
    last_success_at: Optional[float] = None
    latency_ms: Optional[float] = None


# ======================================================================
# 命令系统
# ======================================================================


class CommandResponseStatus(str, Enum):
    """命令响应状态。"""

    PROCESSING = "processing"
    WAITING = "waiting"
    SUCCESS = "success"
    ERROR = "error"
    UNAUTHORIZED = "unauthorized"


class CommandResponse(BaseModel):
    """命令执行响应。"""

    status: CommandResponseStatus
    message: str = ""
    output_segments: List[SendSegment] = Field(default_factory=list)
    wait_options: List[str] = Field(default_factory=list)
    wait_timeout: Optional[float] = None
    callback_cmd: Optional[str] = None
    context_data: Dict[str, Any] = Field(default_factory=dict)
    on_timeout_message: str = "操作超时，已取消"
