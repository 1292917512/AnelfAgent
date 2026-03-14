from __future__ import annotations

from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, Field, PrivateAttr

from agent.core.llm.types import ImageContent
from core.tags import (
    Tag,
    get_time_tag,
    group_id_tag,
    name_tag,
    nickname_tag,
    time_tag,
    uid_tag,
)


class CharType(str, Enum):
    """角色类型。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MsgType(str, Enum):
    """消息字典 key。"""

    ROLE = "role"
    CONTENT = "content"


class Nothing(BaseModel):
    """最小消息基类（仅包含文本与角色）。"""
    model_config = {"arbitrary_types_allowed": True}

    char_type: Optional[CharType] = None
    text_content: str = ""

    def get_agent_dic(self) -> dict:
        if self.char_type is None:
            return {MsgType.CONTENT.value: self.__str__()}
        return {MsgType.ROLE.value: self.char_type.value, MsgType.CONTENT.value: self.__str__()}

    def __str__(self) -> str:
        return self.text_content


class Everything(Nothing):
    """带 uid 的输入/输出消息。"""

    uid: Optional[Union[int, str]] = 0
    images: List[ImageContent] = Field(default_factory=list)
    media_segments: list = Field(default_factory=list)
    tag_list: list[Tag] = Field(default_factory=lambda: [name_tag, uid_tag])
    adapter_key: str = ""
    adapter_message_id: str = ""
    trigger_mind: bool = True
    _tags_generated: bool = PrivateAttr(default=False)

    @property
    def scope_type(self) -> str:
        """返回 scope 类型：'user' 或 'group'。"""
        return "user"

    @property
    def scope_id(self) -> str:
        """返回 scope 标识（uid 或 group_id 的字符串形式）。"""
        return str(self.uid)

    @property
    def entity_scope(self) -> str:
        """返回 'user_123' / 'group_456' 格式的实体 scope。"""
        return f"{self.scope_type}_{self.scope_id}"

    def set_text_content(self, content: str) -> None:
        self.text_content = content
        # 只有当内容包含完整的元数据标签（如 [time:xxx] 或 [uid:xxx]）时才跳过标签生成
        # 简单的 [@me@] 或 [@id:xxx@] 不算元数据标签
        self._tags_generated = bool(
            content
            and content.startswith('[')
            and any(
                f"[{tag}:" in content
                for tag in ("time", "uid", "channel", "group_id")
            )
        )

    def get_text_content(self) -> str:
        return self.text_content

    _tag_field_map: dict[str, str] = {
        "channel": "adapter_key",
        "name": "user_name",
    }

    def get_tag_list(self) -> str:
        text_tags: str = ""
        for tag in self.tag_list:
            tag_name: str = tag.get_tag_name()
            if tag_name == time_tag.get_tag_name():
                text_tags += get_time_tag()
            else:
                field = self._tag_field_map.get(tag_name, tag_name)
                val = getattr(self, field, None)
                if val is not None and val != "":
                    text_tags += tag.generate_label(str(val))
        return text_tags

    def __str__(self) -> str:
        if self._tags_generated:
            return self.text_content
        return self.get_tag_list() + self.text_content


class EverythingGroup(Everything):
    """带 group_id 的输入/输出消息。"""

    group_id: Union[int, str] = 0
    nickname: Optional[str] = ""
    tag_list: list[Tag] = Field(default_factory=lambda: [name_tag, uid_tag, group_id_tag, nickname_tag])

    @property
    def is_group_scope(self) -> bool:
        """是否为有效群聊 scope（group_id 非空非零）。"""
        return self.group_id not in (0, "0", "", None)

    @property
    def scope_type(self) -> str:
        return "group" if self.is_group_scope else "user"

    @property
    def scope_id(self) -> str:
        return str(self.group_id) if self.is_group_scope else str(self.uid)

