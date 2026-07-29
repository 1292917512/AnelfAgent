"""频道系统 — BaseChannel。

每个平台适配器作为"频道"注册，自动将能力声明为 EntityRegistry 工具，
AI 通过两级发现机制按需使用频道功能。
"""

from .base import BaseChannel, ChannelConfig, ChannelMetadata
from .manager import ChannelManager, get_channel_manager
from .pipeline import InputPipeline
from .schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterSendRequest,
    AdapterUser,
    ChannelInfo,
    ChannelType,
    ChannelUser,
    ChannelUserRole,
    CommandResponse,
    CommandResponseStatus,
    HealthStatus,
    MessageSegment,
    SegmentType,
    SendRequest,
    SendResponse,
    SendSegment,
)
from .tool_bridge import channel_tool

__all__ = [
    "AdapterChannel",
    "AdapterMessage",
    "AdapterSendRequest",
    "AdapterUser",
    "BaseChannel",
    "ChannelConfig",
    "ChannelInfo",
    "ChannelManager",
    "ChannelMetadata",
    "ChannelType",
    "ChannelUser",
    "ChannelUserRole",
    "CommandResponse",
    "CommandResponseStatus",
    "HealthStatus",
    "InputPipeline",
    "MessageSegment",
    "SegmentType",
    "SendRequest",
    "SendResponse",
    "SendSegment",
    "channel_tool",
    "get_channel_manager",
]
