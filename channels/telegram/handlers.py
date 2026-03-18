"""入站消息处理器 -- 参照 openclaw bot-handlers.ts。

注册到 python-telegram-bot Application 的各种 Handler。
"""

from __future__ import annotations

import os
import re
import time as _time
from typing import Any, Awaitable, Callable, List, Optional

from core.log import log
from core.tags import forward_tag, reply_to_tag, tag_label

from agent.channel.schemas import (
    AdapterChannel,
    AdapterMessage,
    AdapterUser,
    ChannelType,
    MessageSegment,
    SegmentType,
)
from .context import build_message_context
from .helpers import build_sender_label, strip_bot_mention
from .types import ReplyTarget


def _safe_filename(name: str) -> str:
    """移除文件名中的非法字符。"""
    return re.sub(r'[<>:"/\\|?*]', '_', name)


async def handle_message(
    update: Any,
    context: Any,
    *,
    bot_username: str,
    require_mention: bool,
    on_message: Callable[[AdapterMessage], Awaitable[None]],
) -> None:
    """主消息处理入口。"""
    msg_ctx = build_message_context(update, bot_username, require_mention)
    if msg_ctx is None:
        return

    # 获取机器人 ID 用于识别 @me
    bot_id: int | None = None
    try:
        bot_id = context.bot.id if hasattr(context, "bot") and context.bot else None
    except Exception as e:
        log(f"获取 bot_id 失败: {e}", "DEBUG")

    message = update.effective_message
    content, segments = await _async_extract(message, context, bot_id)
    if not content and not segments:
        log(f"Telegram 消息内容为空，跳过 (chat={msg_ctx.chat_id})", "DEBUG")
        return

    content = strip_bot_mention(content, bot_username)

    adapter_msg = _build_adapter_message(
        update, content,
        segments=segments,
        is_to_me=msg_ctx.was_mentioned,
        trigger_mind=msg_ctx.was_mentioned,
        reply_context=msg_ctx.reply_target,
        forward_origin=msg_ctx.forward_origin,
    )
    log(f"Telegram 分发消息: chat={msg_ctx.chat_id} user={msg_ctx.sender_name} content={content[:100]}")
    await on_message(adapter_msg)


async def handle_callback_query(
    update: Any,
    context: Any,
    *,
    on_message: Callable[[AdapterMessage], Awaitable[None]],
) -> None:
    """内联按钮回调处理。"""
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        await query.answer()
    except Exception as e:
        log(f"回调查询应答失败: {e}", "DEBUG")

    adapter_msg = _build_adapter_message(update, query.data, is_to_me=True)
    await on_message(adapter_msg)


async def handle_edited_message(
    update: Any,
    context: Any,
    *,
    bot_username: str,
    require_mention: bool,
    on_message: Callable[[AdapterMessage], Awaitable[None]],
) -> None:
    """编辑消息处理（当作新消息重新处理）。"""
    msg_ctx = build_message_context(update, bot_username, require_mention)
    if msg_ctx is None:
        return

    message = update.effective_message
    content, segments = await _async_extract(message, context)
    if not content and not segments:
        return

    content = strip_bot_mention(content, bot_username)
    content = f"[编辑] {content}"

    adapter_msg = _build_adapter_message(
        update, content,
        segments=segments,
        is_to_me=msg_ctx.was_mentioned,
        trigger_mind=msg_ctx.was_mentioned,
    )
    log(f"Telegram 编辑消息: chat={msg_ctx.chat_id} content={content[:100]}")
    await on_message(adapter_msg)


async def handle_channel_post(
    update: Any,
    context: Any,
    *,
    on_message: Callable[[AdapterMessage], Awaitable[None]],
) -> None:
    """频道消息处理。"""
    post = update.channel_post
    if not post:
        return

    chat = update.effective_chat
    content, segments = await _async_extract(post, context)
    if not content and not segments:
        return

    sender_id = ""
    sender_name = ""
    if post.sender_chat:
        sender_id = str(post.sender_chat.id)
        sender_name = getattr(post.sender_chat, "title", "") or sender_id
    elif post.from_user:
        sender_id = str(post.from_user.id)
        sender_name = build_sender_label(post.from_user)

    # 注入转发来源
    forward_origin = _extract_forward_origin_from_msg(post)
    if forward_origin:
        content = f"{tag_label(forward_tag.get_tag_name(), forward_origin)}\n{content}" if content else tag_label(forward_tag.get_tag_name(), forward_origin)

    adapter_msg = AdapterMessage(
        message_id=str(post.message_id),
        sender=AdapterUser(
            platform="telegram",
            user_id=sender_id,
            user_name=sender_name,
        ),
        channel=AdapterChannel(
            channel_id=str(chat.id) if chat else sender_id,
            channel_type=ChannelType.GROUP,
            channel_name=getattr(chat, "title", "") or "",
        ),
        content=content,
        segments=segments,
        is_to_me=True,
    )
    await on_message(adapter_msg)


# ------------------------------------------------------------------
# 内部辅助
# ------------------------------------------------------------------

async def _tg_download(
    context: Any,
    file_id: str,
    seg_type: SegmentType,
    unique_id: str = "",
    default_ext: str = "",
    hint_name: str = "",
) -> tuple[str, str]:
    """从 Telegram Bot API 下载文件到本地 workspace/uploads/。

    返回 (local_path, tg_file_path)。失败返回 ("", "")。
    """
    type_dir = {
        SegmentType.IMAGE: "image", SegmentType.VOICE: "voice",
        SegmentType.AUDIO: "audio", SegmentType.VIDEO: "video",
        SegmentType.FILE: "file",
    }.get(seg_type, "file")

    try:
        file = await context.bot.get_file(file_id)
        tg_path = file.file_path or ""

        if hint_name:
            safe_name = _safe_filename(hint_name)
        else:
            ext = default_ext or (tg_path.rsplit(".", 1)[-1] if "." in (tg_path or "") else "bin")
            short = unique_id[:12] if unique_id else file_id[:12]
            safe_name = f"{int(_time.time() * 1000)}_{short}.{ext}"

        dl_dir = os.path.abspath(os.path.join("workspace", "uploads", type_dir))
        os.makedirs(dl_dir, exist_ok=True)
        local_path = os.path.join(dl_dir, f"{int(_time.time() * 1000)}_{safe_name}")
        await file.download_to_drive(local_path)
        return local_path, tg_path
    except Exception as exc:
        log(f"Telegram 下载 {seg_type.value} 失败: {exc}", "WARNING")
        return "", ""


def _parse_mentions(text: str, entities: list, bot_id: int | None = None) -> str:
    """解析消息中的 @ 提及，转换为 [at_uid:xxx] 标准标签。"""
    if not entities or not text:
        return text

    sorted_entities = sorted(entities, key=lambda e: getattr(e, "offset", 0), reverse=True)

    result = text
    for entity in sorted_entities:
        ent_type = getattr(entity, "type", None)
        offset = getattr(entity, "offset", 0)
        length = getattr(entity, "length", 0)
        if ent_type == "text_mention":
            user = getattr(entity, "user", None)
            if user:
                at_text = f"[at_uid:{user.id}]"
                result = result[:offset] + at_text + result[offset + length:]

    return result


async def _async_extract(
    message: Any, context: Any, bot_id: int | None = None
) -> tuple[str, List[MessageSegment]]:
    """异步提取消息内容和媒体段。"""
    segments: List[MessageSegment] = []
    caption = message.caption or ""

    if message.text:
        entities = message.entities or []
        text = _parse_mentions(message.text, entities, bot_id)
        return text, segments

    if message.photo:
        photo = message.photo[-1]
        uid = getattr(photo, "file_unique_id", photo.file_id[:12])
        local, _ = await _tg_download(context, photo.file_id, SegmentType.IMAGE,
                                       unique_id=uid, default_ext="jpg")
        if local:
            segments.append(MessageSegment(
                type=SegmentType.IMAGE, url=local, file_path=local,
                file_name=os.path.basename(local),
            ))
        return caption, segments

    if message.sticker:
        emoji = message.sticker.emoji or ""
        is_animated = getattr(message.sticker, "is_animated", False)
        kind = "动态贴纸" if is_animated else "sticker"
        return f"[{kind} {emoji}]".strip(), segments

    if message.voice:
        uid = getattr(message.voice, "file_unique_id", message.voice.file_id[:12])
        local, tg = await _tg_download(context, message.voice.file_id, SegmentType.VOICE,
                                        unique_id=uid, default_ext="ogg")
        if local:
            segments.append(MessageSegment(
                type=SegmentType.VOICE, url=tg, file_path=local,
                duration=float(getattr(message.voice, "duration", 0)),
                mime_type=getattr(message.voice, "mime_type", "audio/ogg"),
                file_name=os.path.basename(local),
            ))
        return caption, segments

    if message.video:
        uid = getattr(message.video, "file_unique_id", message.video.file_id[:12])
        local, tg = await _tg_download(context, message.video.file_id, SegmentType.VIDEO,
                                        unique_id=uid, default_ext="mp4")
        if local:
            segments.append(MessageSegment(
                type=SegmentType.VIDEO, url=tg, file_path=local,
                duration=float(getattr(message.video, "duration", 0)),
                mime_type=getattr(message.video, "mime_type", "video/mp4"),
                file_name=os.path.basename(local),
            ))
        return caption, segments

    if message.video_note:
        uid = getattr(message.video_note, "file_unique_id", message.video_note.file_id[:12])
        local, tg = await _tg_download(context, message.video_note.file_id, SegmentType.VIDEO,
                                        unique_id=uid, default_ext="mp4")
        if local:
            segments.append(MessageSegment(
                type=SegmentType.VIDEO, url=tg, file_path=local,
                duration=float(getattr(message.video_note, "length", 0)),
                mime_type="video/mp4", file_name=os.path.basename(local),
            ))
        return caption, segments

    if message.animation:
        uid = getattr(message.animation, "file_unique_id", message.animation.file_id[:12])
        local, tg = await _tg_download(context, message.animation.file_id, SegmentType.VIDEO,
                                        unique_id=uid, default_ext="mp4")
        if local:
            segments.append(MessageSegment(
                type=SegmentType.VIDEO, url=tg, file_path=local,
                mime_type=getattr(message.animation, "mime_type", "video/mp4"),
                file_name=os.path.basename(local),
            ))
        return caption or "[动画]", segments

    if message.audio:
        title = getattr(message.audio, "title", None) or getattr(message.audio, "file_name", None) or "audio"
        ext = (getattr(message.audio, "file_name", "") or "audio.mp3").rsplit(".", 1)[-1] or "mp3"
        local, tg = await _tg_download(context, message.audio.file_id, SegmentType.AUDIO,
                                        hint_name=f"{_safe_filename(title)}.{ext}")
        if local:
            segments.append(MessageSegment(
                type=SegmentType.AUDIO, url=tg, file_path=local,
                duration=float(getattr(message.audio, "duration", 0)),
                mime_type=getattr(message.audio, "mime_type", "audio/mpeg"),
                file_name=os.path.basename(local),
            ))
        return caption, segments

    if message.document:
        fname = getattr(message.document, "file_name", None) or "unknown"
        local, tg = await _tg_download(context, message.document.file_id, SegmentType.FILE,
                                        hint_name=_safe_filename(fname))
        if local:
            segments.append(MessageSegment(
                type=SegmentType.FILE, url=tg, file_path=local,
                file_name=fname,
                mime_type=getattr(message.document, "mime_type", ""),
            ))
        return caption, segments

    if message.location:
        loc = message.location
        lat, lng = loc.latitude, loc.longitude
        segments.append(MessageSegment(
            type=SegmentType.LOCATION,
            content=f"{lat},{lng}",
        ))
        return f"[位置: {lat}, {lng}]", segments

    if message.contact:
        c = message.contact
        name = f"{c.first_name or ''} {c.last_name or ''}".strip()
        return f"[联系人: {name} {c.phone_number or ''}]", segments

    if message.poll:
        poll = message.poll
        options = " / ".join(
            getattr(o, "text", str(o)) for o in (poll.options or [])
        )
        return f"[投票: {poll.question}] 选项: {options}", segments

    if message.dice:
        emoji = getattr(message.dice, "emoji", "🎲")
        value = getattr(message.dice, "value", "?")
        return f"[骰子: {emoji} 结果={value}]", segments

    if getattr(message, "forward_date", None):
        return caption or "[转发消息]", segments

    return caption, segments


def _extract_forward_origin_from_msg(msg: Any) -> Optional[str]:
    """从消息中提取转发来源名称。"""
    origin = getattr(msg, "forward_origin", None)
    if origin:
        origin_type = getattr(origin, "type", None)
        if origin_type == "user":
            return build_sender_label(getattr(origin, "sender_user", None))
        if origin_type in ("chat", "channel"):
            chat = getattr(origin, "sender_chat", None) or getattr(origin, "chat", None)
            return getattr(chat, "title", None) if chat else None
    fwd_from = getattr(msg, "forward_from", None)
    if fwd_from:
        return build_sender_label(fwd_from)
    fwd_chat = getattr(msg, "forward_from_chat", None)
    if fwd_chat:
        return getattr(fwd_chat, "title", None) or str(fwd_chat.id)
    return None


def _build_adapter_message(
    update: Any,
    content: str,
    *,
    segments: Optional[List[MessageSegment]] = None,
    is_to_me: bool = False,
    trigger_mind: bool = True,
    reply_context: Optional[ReplyTarget] = None,
    forward_origin: Optional[str] = None,
) -> AdapterMessage:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    user_id = str(user.id) if user else "unknown"
    user_name = build_sender_label(user) if user else ""

    is_group = chat.type in ("group", "supergroup") if chat else False
    channel_id = str(chat.id) if chat else user_id

    reply_to_id = ""

    # 注入回复引用上下文
    if reply_context and reply_context.id:
        reply_to_id = str(reply_context.id)
        reply_label = tag_label(reply_to_tag.get_tag_name(), reply_to_id)
        body_preview = reply_context.body[:200] if reply_context.body else ""
        reply_header = f"{reply_label}{reply_context.sender}: {body_preview}"
        content = f"{reply_header}\n{content}" if content else reply_header

    # 注入转发来源
    if forward_origin:
        fwd_label = tag_label(forward_tag.get_tag_name(), forward_origin)
        content = f"{fwd_label}\n{content}" if content else fwd_label

    return AdapterMessage(
        message_id=str(message.message_id) if message else "",
        sender=AdapterUser(
            platform="telegram",
            user_id=user_id,
            user_name=user_name,
        ),
        channel=AdapterChannel(
            channel_id=channel_id,
            channel_type=ChannelType.GROUP if is_group else ChannelType.PRIVATE,
            channel_name=getattr(chat, "title", "") or "" if chat else "",
        ),
        content=content,
        segments=segments or [],
        is_to_me=is_to_me,
        trigger_mind=trigger_mind,
        reply_to_id=reply_to_id,
    )
