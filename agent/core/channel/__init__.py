"""频道系统 -- 替代 respond 模块。

每个平台适配器作为"频道"注册，自动将能力声明为 EntityRegistry 工具，
AI 通过两级发现机制按需使用频道功能。
"""

from .channel import BaseChannel, ChannelCapability, ChannelStatus
from .manager import ChannelManager, get_channel_manager
from .pipeline import InputPipeline
from .schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterSendRequest,
    AdapterUser,
    ChannelType,
    MessageSegment,
    SegmentType,
    SendSegment,
)

__all__ = [
    "BaseChannel",
    "ChannelCapability",
    "ChannelStatus",
    "ChannelManager",
    "get_channel_manager",
    "InputPipeline",
    "AdapterChannel",
    "AdapterMessage",
    "AdapterSendRequest",
    "AdapterUser",
    "ChannelType",
    "MessageSegment",
    "SegmentType",
    "SendSegment",
]
