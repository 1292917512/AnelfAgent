from __future__ import annotations

from typing import Optional, Union

from pydantic import Field

from .everything import CharType, Everything, EverythingGroup, Nothing
from core.tags import (
    Tag,
    channel_tag,
    group_id_tag,
    message_id_tag,
    name_tag,
    nickname_tag,
    reply_to_tag,
    session_id_tag,
    time_tag,
    uid_tag,
)


class MessageAssistant(Everything):
    uid: Optional[Union[int, str]] = 0
    tag_list: list[Tag] = Field(default_factory=list)
    char_type: CharType = CharType.ASSISTANT


class MessageAssistantGroup(EverythingGroup):
    uid: Optional[Union[int, str]] = 0
    group_id: Union[int, str] = 0
    tag_list: list[Tag] = Field(default_factory=list)
    char_type: CharType = CharType.ASSISTANT


class MessageUser(Everything):
    user_name: str = ""
    char_type: CharType = CharType.USER
    tag_list: list[Tag] = Field(default_factory=lambda: [time_tag, channel_tag, session_id_tag, message_id_tag, uid_tag, name_tag, reply_to_tag])


class MessageGroupUser(EverythingGroup):
    user_name: str = ""
    char_type: CharType = CharType.USER
    to_me: bool = False
    tag_list: list[Tag] = Field(default_factory=lambda: [time_tag, channel_tag, session_id_tag, message_id_tag, group_id_tag, uid_tag, name_tag, nickname_tag, reply_to_tag])


class MessageQuestion(Nothing):
    char_type: CharType = CharType.USER


class MessageToolResult(Everything):
    """工具/后台任务结果消息，以 user 角色写入对话历史并触发新一轮思考。"""
    char_type: CharType = CharType.USER
    tag_list: list[Tag] = Field(default_factory=list)
    trigger_mind: bool = True


class MessageMemory(Everything):
    tag_list: list[Tag] = Field(default_factory=lambda: [time_tag])
    char_type: CharType = CharType.SYSTEM

