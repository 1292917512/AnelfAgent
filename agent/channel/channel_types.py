"""频道类型定义 — ChannelCapability / ChannelStatus / 工具函数。"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Optional


class ChannelStatus(str, Enum):
    """频道运行状态。"""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class ChannelCapability(str, Enum):
    """频道能力枚举 — 声明频道支持的操作。"""

    # 发送类
    SEND_TEXT = "send_text"
    SEND_PHOTO = "send_photo"
    SEND_VIDEO = "send_video"
    SEND_AUDIO = "send_audio"
    SEND_VOICE = "send_voice"
    SEND_FILE = "send_file"
    SEND_LOCATION = "send_location"
    SEND_ANIMATION = "send_animation"
    SEND_CONTACT = "send_contact"
    SEND_POLL = "send_poll"
    # 消息操作
    EDIT_MESSAGE = "edit_message"
    DELETE_MESSAGE = "delete_message"
    FORWARD_MESSAGE = "forward_message"
    PIN_MESSAGE = "pin_message"
    UNPIN_MESSAGE = "unpin_message"
    # 信息查询
    GET_CHAT_INFO = "get_chat_info"
    GET_CHAT_MEMBERS = "get_chat_members"
    GET_CHAT_ADMINS = "get_chat_admins"
    LIST_KNOWN_CHATS = "list_known_chats"
    # 群管理
    BAN_USER = "ban_user"
    UNBAN_USER = "unban_user"
    SET_CHAT_TITLE = "set_chat_title"
    SET_CHAT_DESCRIPTION = "set_chat_description"
    # 互动
    MESSAGE_REACTION = "message_reaction"
    # 高级
    REPLY_TO = "reply_to"
    INLINE_KEYBOARD = "inline_keyboard"
    STREAMING = "streaming"


def _ok(data: Optional[dict] = None) -> str:
    """构造成功响应 JSON。"""
    return json.dumps({"success": True, **(data or {})}, ensure_ascii=False)


def _err(msg: str) -> str:
    """构造错误响应 JSON。"""
    return json.dumps({"success": False, "error": msg}, ensure_ascii=False)


_AT_RE = re.compile(r'\[at_uid:([^\]]+)\]')


def normalize_at_mentions(text: str) -> str:
    """将 [at_uid:xxx] 转为纯文本 @uid。"""
    def _replacer(m: re.Match[str]) -> str:
        uid = m.group(1)
        if uid == "all":
            return "@全体成员"
        return f"@{uid}"
    return _AT_RE.sub(_replacer, text)
