"""消息上下文构建 -- 参照 openclaw bot-message-context.ts。

从 Telegram Update 中提取结构化上下文：提及检测、回复链解析、媒体识别。
"""

from __future__ import annotations

from typing import Any, Optional

from .helpers import (
    build_sender_label,
    has_bot_mention,
    has_entity_mention,
    resolve_thread_spec,
    strip_bot_mention,
)
from .types import ReplyTarget, TelegramMessageContext


def build_message_context(
    update: Any,
    bot_username: str,
    require_mention: bool,
) -> Optional[TelegramMessageContext]:
    """从 Telegram Update 构建消息上下文。不满足过滤条件时返回 None。"""
    message = update.effective_message
    if not message:
        return None

    chat = update.effective_chat
    user = update.effective_user
    if not chat:
        return None

    chat_id = chat.id
    is_group = chat.type in ("group", "supergroup")
    is_forum = getattr(chat, "is_forum", False) or False
    message_thread_id = getattr(message, "message_thread_id", None)
    thread_spec = resolve_thread_spec(is_group, is_forum, message_thread_id)

    sender_id = str(user.id) if user else ""
    sender_name = build_sender_label(user)

    raw_text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []

    explicit_mention = has_bot_mention(raw_text, bot_username)
    any_mention = has_entity_mention(entities)

    bot_id = None
    try:
        bot_id = update.get_bot().id if hasattr(update, "get_bot") else None
    except Exception:
        pass  # bot_id 仅用于检测 implicit_mention，获取失败不影响消息处理
    reply_msg = message.reply_to_message
    reply_from_id = reply_msg.from_user.id if reply_msg and reply_msg.from_user else None
    implicit_mention = (
        is_group and bot_id is not None and reply_from_id == bot_id
    )

    # 私聊始终视为"to me"；群聊才受 require_mention 约束
    was_mentioned = not is_group or explicit_mention or implicit_mention or (not require_mention)

    reply_target = _describe_reply_target(reply_msg) if reply_msg else None

    content = strip_bot_mention(raw_text, bot_username)
    forward_origin = _extract_forward_origin(message)

    return TelegramMessageContext(
        chat_id=chat_id,
        is_group=is_group,
        is_forum=is_forum,
        thread_spec=thread_spec,
        sender_id=sender_id,
        sender_name=sender_name,
        reply_target=reply_target,
        was_mentioned=was_mentioned,
        implicit_mention=implicit_mention,
        raw_text=content,
        message_id=message.message_id,
        forward_origin=forward_origin,
    )


def _describe_reply_target(reply_msg: Any) -> Optional[ReplyTarget]:
    """解析被回复的消息。"""
    if not reply_msg:
        return None

    body = (reply_msg.text or reply_msg.caption or "").strip()
    if not body:
        body = _media_placeholder(reply_msg) or ""
    if not body:
        return None

    sender = build_sender_label(reply_msg.from_user) if reply_msg.from_user else "unknown"
    quote_text = getattr(reply_msg, "quote", None)
    kind = "quote" if quote_text else "reply"

    forwarded_from = _extract_forward_origin(reply_msg)

    return ReplyTarget(
        id=str(reply_msg.message_id) if reply_msg.message_id else None,
        sender=sender,
        body=body[:500],
        kind=kind,
        forwarded_from=forwarded_from,
    )


def _media_placeholder(msg: Any) -> Optional[str]:
    if msg.photo:
        return "<media:photo>"
    if msg.video:
        return "<media:video>"
    if msg.audio:
        return "<media:audio>"
    if msg.voice:
        return "<media:voice>"
    if msg.document:
        return "<media:document>"
    if msg.sticker:
        return "<media:sticker>"
    if msg.animation:
        return "<media:animation>"
    return None


def _extract_forward_origin(msg: Any) -> Optional[str]:
    origin = getattr(msg, "forward_origin", None)
    if not origin:
        fwd_from = getattr(msg, "forward_from", None)
        if fwd_from:
            return build_sender_label(fwd_from)
        fwd_chat = getattr(msg, "forward_from_chat", None)
        if fwd_chat:
            return getattr(fwd_chat, "title", None) or str(fwd_chat.id)
        return None
    origin_type = getattr(origin, "type", None)
    if origin_type == "user":
        return build_sender_label(getattr(origin, "sender_user", None))
    if origin_type == "chat":
        chat = getattr(origin, "sender_chat", None)
        return getattr(chat, "title", None) if chat else None
    if origin_type == "channel":
        chat = getattr(origin, "chat", None)
        return getattr(chat, "title", None) if chat else None
    return None
