"""Messages：统一消息模型（平台消息、内部事件、LLM messages）。"""

from .characters import CharacterAgent, EntityData
from .everything import (
    CharType,
    Everything,
    EverythingGroup,
    MsgType,
    Nothing,
    build_entity_scope,
    build_scope_id,
    parse_entity_scope,
)
from .presets import (
    MessageAssistant,
    MessageAssistantGroup,
    MessageGroupUser,
    MessageMemory,
    MessageQuestion,
    MessageToolResult,
    MessageUser,
)

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
    "parse_entity_scope",
    "build_entity_scope",
    "build_scope_id",
]

