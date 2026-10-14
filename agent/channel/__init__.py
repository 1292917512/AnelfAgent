"""频道系统 — BaseChannel。

每个平台适配器作为"频道"注册，自动将能力声明为 EntityRegistry 工具，
AI 通过两级发现机制按需使用频道功能。
"""

from agent.llm.model_api import (
    ChatResult,
    ChoiceQuestion,
    ImageContent,
    JsonValue,
    JudgmentError,
    JudgmentReport,
    LLMNotConfiguredError,
    ModelAPI,
    ModelCallError,
    ModelResult,
    ModelType,
    MusicResult,
    NoulCriteria,
    NoulQuestion,
    Question,
    ScoreQuestion,
    VideoContent,
    get_model_api,
)
from core.network import (
    AsyncNetworkTransport,
    NetworkProbeRequest,
    NetworkRoute,
    NetworkTransport,
    ProxyMode,
    async_network_client,
    diagnose_network,
    get_proxy_status,
    get_proxy_url,
    network_client,
    open_proxy_socket,
    resolve_network_route,
    resolve_socket_route,
)

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
    "get_model_api", "ModelAPI", "ModelType", "ModelResult", "ModelCallError", "LLMNotConfiguredError",
    "ChatResult", "ImageContent", "VideoContent", "MusicResult",
    "ChoiceQuestion", "NoulQuestion", "NoulCriteria", "ScoreQuestion", "Question",
    "JsonValue", "JudgmentReport", "JudgmentError",
    "NetworkProbeRequest", "diagnose_network", "get_proxy_status",
    "AsyncNetworkTransport", "NetworkTransport", "NetworkRoute", "ProxyMode",
    "async_network_client", "network_client", "get_proxy_url", "resolve_network_route",
    "open_proxy_socket", "resolve_socket_route",
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
