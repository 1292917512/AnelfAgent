from __future__ import annotations

import time
from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, Field, PrivateAttr

from agent.llm.types import ImageContent
from core.tags import (
    Tag,
    get_time_tag,
    group_id_tag,
    name_tag,
    nickname_tag,
    reply_to_tag,
    tag_label,
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
    session_id: str = ""
    reply_to_id: str = ""
    reply_content: str = ""
    trigger_mind: bool = True
    # 消息到达时间（纳秒）：构造即到达，时间标签与对话历史入库均以它为准，保证时序
    created_ts_ns: int = Field(default_factory=time.time_ns)
    _tags_generated: bool = PrivateAttr(default=False)

    @property
    def scope_type(self) -> str:
        """返回 scope 类型：'user' 或 'group'。"""
        return "user"

    def _session_suffix(self, base_id: str) -> str:
        """返回 session 后缀（``"#chat_id"``），非子会话时返回空串。

        仅当 session_id 是真正的子会话标识（非空且不同于自然 scope id）时才拼后缀：
        webui 多标签页 chat_id 产生新键；telegram 私聊（session_id==uid）/
        群聊（session_id==group_id）与默认会话保持不变，保证 DB 键连续。
        """
        session = str(self.session_id or "")
        if session and session != base_id:
            return f"#{session}"
        return ""

    @property
    def scope_id(self) -> str:
        """返回 scope 标识（uid 或 group_id 的字符串形式，含子会话后缀）。"""
        base = str(self.uid)
        return f"{base}{self._session_suffix(base)}"

    @property
    def entity_scope(self) -> str:
        """返回 'user_123' / 'group_456' / 'user_123#chat_id' 格式的实体 scope。

        直接基于 ``scope_id``（已含 ``session_id`` 后缀）构造，保证一致性。
        """
        return f"{self.scope_type}_{self.scope_id}"

    def set_text_content(self, content: str) -> None:
        self.text_content = content
        # 只有当内容包含完整的元数据标签（如 [time:xxx] 或 [uid:xxx]）时才跳过标签生成
        # 简单的 [at_uid:xxx] 不算元数据标签
        self._tags_generated = bool(
            content
            and content.startswith('[')
            and any(
                f"[{tag}:" in content
                for tag in ("time", "uid", "channel", "group_id", "session_id", "message_id")
            )
        )

    def get_text_content(self) -> str:
        return self.text_content

    _tag_field_map: dict[str, str] = {
        "channel": "adapter_key",
        "name": "user_name",
        "message_id": "adapter_message_id",
        "reply_to": "reply_to_id",
    }

    def get_tag_list(self) -> str:
        text_tags: str = ""
        for tag in self.tag_list:
            tag_name: str = tag.get_tag_name()
            if tag_name == time_tag.get_tag_name():
                text_tags += get_time_tag(self.created_ts_ns)
            elif tag_name == reply_to_tag.get_tag_name():
                text_tags += self._render_reply_to_label()
            else:
                field = self._tag_field_map.get(tag_name, tag_name)
                val = getattr(self, field, None)
                if val is not None and val != "":
                    text_tags += tag.generate_label(str(val))
        return text_tags

    def _render_reply_to_label(self) -> str:
        """渲染 [reply_to:xxx] 标签及引用预览（压缩空白后截取前 200 字符）。"""
        if not self.reply_to_id:
            return ""
        header = tag_label(reply_to_tag.get_tag_name(), str(self.reply_to_id))
        preview = " ".join((self.reply_content or "").split()).strip()
        if preview:
            header = f"{header}{preview[:200]}"
        return f"{header}\n" if self.text_content else header

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
        """群聊以 group_id 为基，私聊回退 uid；均按统一规则拼子会话后缀。"""
        base = str(self.group_id) if self.is_group_scope else str(self.uid)
        return f"{base}{self._session_suffix(base)}"


def parse_entity_scope(scope: str) -> tuple[str, str, str]:
    """解析实体 scope，返回 (scope_type, base_id, session_id)。

    支持 ``user_123`` / ``group_456`` / ``user_123#chat_id`` 格式；
    无法识别时返回 ("", "", "")。
    """
    if not scope or "_" not in scope:
        return "", "", ""
    scope_type, raw_id = scope.split("_", 1)
    if scope_type not in ("user", "group") or not raw_id:
        return "", "", ""
    if "#" in raw_id:
        base_id, session_id = raw_id.split("#", 1)
    else:
        base_id, session_id = raw_id, ""
    if not base_id:
        return "", "", ""
    return scope_type, base_id, session_id

