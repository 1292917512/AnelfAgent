"""Telegram 工具函数 -- 话题解析、键盘构建、文本处理。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .types import InlineKeyboard, ThreadSpec

GENERAL_TOPIC_ID = 1


def resolve_thread_spec(
    is_group: bool, is_forum: bool, message_thread_id: Optional[int],
) -> ThreadSpec:
    """解析 Telegram 线程/话题标识。"""
    if not is_group:
        if message_thread_id is not None:
            return ThreadSpec(id=message_thread_id, scope="dm")
        return ThreadSpec(scope="none")
    if not is_forum:
        return ThreadSpec(scope="none")
    if message_thread_id is None:
        return ThreadSpec(id=GENERAL_TOPIC_ID, scope="forum")
    return ThreadSpec(id=message_thread_id, scope="forum")


def build_thread_params(thread: Optional[ThreadSpec]) -> Dict[str, Any]:
    """构建 Telegram API 的 message_thread_id 参数。"""
    if thread and thread.id is not None and thread.scope == "forum":
        return {"message_thread_id": thread.id}
    return {}


def build_peer_id(chat_id: int, thread_id: Optional[int] = None) -> str:
    if thread_id is not None:
        return f"telegram:{chat_id}:topic:{thread_id}"
    return f"telegram:{chat_id}"


def build_sender_label(user: Any) -> str:
    if user is None:
        return "unknown"
    full = getattr(user, "full_name", None)
    if full:
        return full
    uname = getattr(user, "username", None)
    if uname:
        return uname
    uid = getattr(user, "id", None)
    return str(uid) if uid else "unknown"


def build_inline_keyboard(buttons: Optional[InlineKeyboard]) -> Optional[Any]:
    """从按钮定义构建 python-telegram-bot InlineKeyboardMarkup。"""
    if not buttons:
        return None
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        rows = []
        for row in buttons:
            tg_row = []
            for btn in row:
                text = btn.get("text", "")
                callback_data = btn.get("callback_data", "")
                url = btn.get("url")
                if not text:
                    continue
                if url:
                    tg_row.append(InlineKeyboardButton(text=text, url=url))
                elif callback_data:
                    tg_row.append(InlineKeyboardButton(text=text, callback_data=callback_data))
            if tg_row:
                rows.append(tg_row)
        return InlineKeyboardMarkup(rows) if rows else None
    except ImportError:
        return None


def split_caption(text: str, limit: int = 1024) -> tuple[str, str]:
    """将文本分为 caption（<=limit）和后续文本。"""
    if not text or len(text) <= limit:
        return text, ""
    cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut], text[cut:].lstrip("\n")


def strip_bot_mention(text: str, bot_username: str) -> str:
    if bot_username:
        text = text.replace(f"@{bot_username}", "").strip()
    return text


def has_bot_mention(text: str, bot_username: str) -> bool:
    if not bot_username:
        return False
    return f"@{bot_username}" in text


def has_entity_mention(entities: Optional[list]) -> bool:
    """检查消息实体中是否包含 mention 类型。"""
    if not entities:
        return False
    return any(getattr(e, "type", None) == "mention" for e in entities)
