"""Messages：统一消息模型（平台消息、内部事件、LLM messages）。"""

from .everything import CharType, Everything, EverythingGroup, MsgType, Nothing
from .presets import (
    MessageAssistant,
    MessageAssistantGroup,
    MessageGroupUser,
    MessageMemory,
    MessageQuestion,
    MessageToolResult,
    MessageUser,
)
from .characters import CharacterAgent, EntityData

__all__ = [
    "CharType",
    "MsgType",
    "Nothing",
    "Everything",
    "EverythingGroup",
    "MessageAssistant",
    "MessageAssistantGroup",
    "MessageUser",
    "MessageGroupUser",
    "MessageQuestion",
    "MessageToolResult",
    "MessageMemory",
    "CharacterAgent",
    "EntityData",
]

