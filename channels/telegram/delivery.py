"""统一消息发送入口 -- 参照 openclaw bot/delivery.ts。

处理：文本分块、reply_to 策略、媒体发送、内联键盘、格式化。
"""

from __future__ import annotations

from typing import Any, List, Optional, Union

from core.log import log

from . import send as tg_send
from .format import chunk_html_text, markdown_to_telegram_html, plain_fallback
from .helpers import build_inline_keyboard, split_caption
from .types import DeliveryResult, InlineKeyboard, ThreadSpec


async def deliver_reply(
    bot: Any,
    chat_id: Union[str, int],
    content: str,
    *,
    reply_to: Optional[str] = None,
    reply_to_mode: str = "first",
    media_files: Optional[List[str]] = None,
    thread: Optional[ThreadSpec] = None,
    parse_mode: str = "html",
    buttons: Optional[InlineKeyboard] = None,
    text_limit: int = 4096,
    link_preview: bool = True,
) -> DeliveryResult:
    """统一的消息发送入口。

    Args:
        bot: Telegram Bot 实例
        chat_id: 目标会话 ID
        content: Markdown 文本内容
        reply_to: 要回复的消息 ID
        reply_to_mode: "first"=仅首条挂引用 / "all"=全部 / "off"=不引用
        media_files: 本地媒体文件路径列表
        thread: 论坛话题
        parse_mode: "html" / "plain"
        buttons: 内联键盘按钮
        text_limit: 单条消息字符限制
        link_preview: 链接预览
    """
    result = DeliveryResult()
    has_replied = False

    reply_to_id = _resolve_reply_id(reply_to) if reply_to_mode != "off" else None
    reply_markup = build_inline_keyboard(buttons)

    # 纯文本（无媒体）
    if not media_files:
        chunks = chunk_html_text(content, text_limit)
        for i, chunk in enumerate(chunks):
            r2id = reply_to_id if (reply_to_mode == "all" or not has_replied) else None
            text = chunk["html"] if parse_mode == "html" else chunk["text"]
            pm = "HTML" if parse_mode == "html" else None
            first_chunk_markup = reply_markup if i == 0 else None

            msg_id = await tg_send.send_text(
                bot, chat_id, text,
                parse_mode=pm,
                reply_to_message_id=r2id,
                thread=thread,
                reply_markup=first_chunk_markup,
                link_preview=link_preview,
            )
            result.message_ids.append(msg_id)
            result.delivered = True
            if r2id and not has_replied:
                has_replied = True
        return result

    # 媒体 + 可选 caption
    import mimetypes
    first_media = True
    for file_path in media_files:
        mime, _ = mimetypes.guess_type(file_path)
        mime = mime or "application/octet-stream"

        caption_text, follow_up = split_caption(content if first_media else "", 1024)
        html_caption = markdown_to_telegram_html(caption_text) if caption_text and parse_mode == "html" else caption_text

        r2id = reply_to_id if (reply_to_mode == "all" or not has_replied) else None
        pm = "HTML" if parse_mode == "html" and html_caption else None
        first_media_markup = reply_markup if first_media else None

        with open(file_path, "rb") as f:
            file_data = f.read()

        send_fn, send_kwargs = _resolve_media_sender(mime)
        msg_id = await send_fn(
            bot, chat_id, file_data,
            caption=html_caption or None,
            parse_mode=pm,
            reply_to_message_id=r2id,
            thread=thread,
            **({} if first_media_markup is None else {"reply_markup": first_media_markup}),
            **send_kwargs,
        )
        result.message_ids.append(msg_id)
        result.delivered = True

        if r2id and not has_replied:
            has_replied = True

        if follow_up and first_media:
            follow_chunks = chunk_html_text(follow_up, text_limit)
            for chunk in follow_chunks:
                text = chunk["html"] if parse_mode == "html" else chunk["text"]
                pm2 = "HTML" if parse_mode == "html" else None
                mid = await tg_send.send_text(
                    bot, chat_id, text,
                    parse_mode=pm2,
                    reply_to_message_id=r2id,
                    thread=thread,
                    link_preview=link_preview,
                )
                result.message_ids.append(mid)

        first_media = False

    return result


def _resolve_reply_id(reply_to: Optional[str]) -> Optional[int]:
    if not reply_to:
        return None
    try:
        return int(reply_to)
    except (ValueError, TypeError):
        return None


def _resolve_media_sender(mime: str):
    """根据 MIME 类型选择发送函数。"""
    if mime.startswith("image/gif") or mime == "video/mp4":
        if mime.startswith("image/gif"):
            return tg_send.send_animation, {}
        return tg_send.send_video, {}
    if mime.startswith("image/"):
        return tg_send.send_photo, {}
    if mime.startswith("video/"):
        return tg_send.send_video, {}
    if mime.startswith("audio/"):
        if "ogg" in mime or "opus" in mime:
            return tg_send.send_voice, {}
        return tg_send.send_audio, {}
    return tg_send.send_file, {}
