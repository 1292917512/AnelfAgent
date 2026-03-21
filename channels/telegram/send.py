"""底层 Telegram Bot API 封装。

每个方法内置：HTML 格式回退、线程参数注入、重试逻辑。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

from core.log import log

from .errors import (
    TelegramSendError,
    is_chat_migrated,
    is_forbidden,
    is_html_parse_error,
    is_thread_not_found,
    with_retry,
)
from .helpers import build_inline_keyboard, build_thread_params
from .types import InlineKeyboard, ThreadSpec


async def send_text(
    bot: Any,
    chat_id: Union[str, int],
    text: str,
    *,
    parse_mode: Optional[str] = "HTML",
    reply_to_message_id: Optional[int] = None,
    thread: Optional[ThreadSpec] = None,
    reply_markup: Any = None,
    link_preview: bool = True,
    buttons: Optional[InlineKeyboard] = None,
) -> int:
    """发送文本消息，返回 message_id。"""
    params: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        **build_thread_params(thread),
    }
    if parse_mode:
        params["parse_mode"] = parse_mode
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True
    if not link_preview:
        params["disable_web_page_preview"] = True

    markup = reply_markup or build_inline_keyboard(buttons)
    if markup:
        params["reply_markup"] = markup

    async def _send() -> int:
        try:
            msg = await bot.send_message(**params)
            return msg.message_id
        except Exception as exc:
            new_id = is_chat_migrated(exc)
            if new_id is not None:
                log(f"群迁移: {chat_id} → {new_id}", "WARNING")
                params["chat_id"] = new_id
                msg = await bot.send_message(**params)
                return msg.message_id
            if is_html_parse_error(exc) and parse_mode:
                log("HTML 解析失败，回退纯文本", "WARNING")
                params.pop("parse_mode", None)
                msg = await bot.send_message(**params)
                return msg.message_id
            if is_thread_not_found(exc) and "message_thread_id" in params:
                log("话题不存在，移除 thread_id 重试", "WARNING")
                params.pop("message_thread_id", None)
                msg = await bot.send_message(**params)
                return msg.message_id
            raise

    return await with_retry(_send, label="sendText")


async def send_photo(
    bot: Any,
    chat_id: Union[str, int],
    photo: Any,
    *,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    reply_to_message_id: Optional[int] = None,
    thread: Optional[ThreadSpec] = None,
    reply_markup: Any = None,
) -> int:
    params: Dict[str, Any] = {"chat_id": chat_id, "photo": photo, **build_thread_params(thread)}
    if caption:
        params["caption"] = caption
    if parse_mode and caption:
        params["parse_mode"] = parse_mode
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True
    if reply_markup:
        params["reply_markup"] = reply_markup

    async def _send() -> int:
        msg = await bot.send_photo(**params)
        return msg.message_id

    return await with_retry(_send, label="sendPhoto")


async def send_video(
    bot: Any,
    chat_id: Union[str, int],
    video: Any,
    *,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    reply_to_message_id: Optional[int] = None,
    thread: Optional[ThreadSpec] = None,
    reply_markup: Any = None,
) -> int:
    params: Dict[str, Any] = {"chat_id": chat_id, "video": video, **build_thread_params(thread)}
    if caption:
        params["caption"] = caption
    if parse_mode and caption:
        params["parse_mode"] = parse_mode
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True
    if reply_markup:
        params["reply_markup"] = reply_markup

    async def _send() -> int:
        msg = await bot.send_video(**params)
        return msg.message_id

    return await with_retry(_send, label="sendVideo")


async def send_audio(
    bot: Any,
    chat_id: Union[str, int],
    audio: Any,
    *,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    reply_to_message_id: Optional[int] = None,
    thread: Optional[ThreadSpec] = None,
) -> int:
    params: Dict[str, Any] = {"chat_id": chat_id, "audio": audio, **build_thread_params(thread)}
    if caption:
        params["caption"] = caption
    if parse_mode and caption:
        params["parse_mode"] = parse_mode
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True

    async def _send() -> int:
        msg = await bot.send_audio(**params)
        return msg.message_id

    return await with_retry(_send, label="sendAudio")


async def send_voice(
    bot: Any,
    chat_id: Union[str, int],
    voice: Any,
    *,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    reply_to_message_id: Optional[int] = None,
    thread: Optional[ThreadSpec] = None,
) -> int:
    params: Dict[str, Any] = {"chat_id": chat_id, "voice": voice, **build_thread_params(thread)}
    if caption:
        params["caption"] = caption
    if parse_mode and caption:
        params["parse_mode"] = parse_mode
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True

    async def _send() -> int:
        msg = await bot.send_voice(**params)
        return msg.message_id

    return await with_retry(_send, label="sendVoice")


async def send_file(
    bot: Any,
    chat_id: Union[str, int],
    file_path: Any,
    *,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    reply_to_message_id: Optional[int] = None,
    thread: Optional[ThreadSpec] = None,
) -> int:
    params: Dict[str, Any] = {"chat_id": chat_id, "document": file_path, **build_thread_params(thread)}
    if caption:
        params["caption"] = caption
    if parse_mode and caption:
        params["parse_mode"] = parse_mode
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True

    async def _send() -> int:
        msg = await bot.send_document(**params)
        return msg.message_id

    return await with_retry(_send, label="sendFile")


async def send_animation(
    bot: Any,
    chat_id: Union[str, int],
    animation: Any,
    *,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "HTML",
    reply_to_message_id: Optional[int] = None,
    thread: Optional[ThreadSpec] = None,
) -> int:
    params: Dict[str, Any] = {"chat_id": chat_id, "animation": animation, **build_thread_params(thread)}
    if caption:
        params["caption"] = caption
    if parse_mode and caption:
        params["parse_mode"] = parse_mode
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True

    async def _send() -> int:
        msg = await bot.send_animation(**params)
        return msg.message_id

    return await with_retry(_send, label="sendAnimation")


async def send_location(
    bot: Any,
    chat_id: Union[str, int],
    latitude: float,
    longitude: float,
    *,
    reply_to_message_id: Optional[int] = None,
    thread: Optional[ThreadSpec] = None,
) -> int:
    params: Dict[str, Any] = {
        "chat_id": chat_id,
        "latitude": latitude,
        "longitude": longitude,
        **build_thread_params(thread),
    }
    if reply_to_message_id:
        params["reply_to_message_id"] = reply_to_message_id
        params["allow_sending_without_reply"] = True

    async def _send() -> int:
        msg = await bot.send_location(**params)
        return msg.message_id

    return await with_retry(_send, label="sendLocation")


# ------------------------------------------------------------------
# 消息编辑 / 删除
# ------------------------------------------------------------------

async def edit_message_text(
    bot: Any,
    chat_id: Union[str, int],
    message_id: int,
    text: str,
    *,
    parse_mode: Optional[str] = "HTML",
    reply_markup: Any = None,
    link_preview: bool = True,
) -> bool:
    params: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if parse_mode:
        params["parse_mode"] = parse_mode
    if not link_preview:
        params["disable_web_page_preview"] = True
    if reply_markup:
        params["reply_markup"] = reply_markup

    try:
        await bot.edit_message_text(**params)
        return True
    except Exception as exc:
        if "message is not modified" in str(exc).lower():
            return True
        if is_html_parse_error(exc) and parse_mode:
            params.pop("parse_mode", None)
            try:
                await bot.edit_message_text(**params)
                return True
            except Exception:
                pass
        log(f"editMessageText 失败: {exc}", "WARNING")
        return False


async def delete_message(
    bot: Any,
    chat_id: Union[str, int],
    message_id: int,
) -> bool:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as exc:
        log(f"deleteMessage 失败: {exc}", "WARNING")
        return False


# ------------------------------------------------------------------
# Chat Action（打字指示器）
# ------------------------------------------------------------------

async def send_chat_action(
    bot: Any,
    chat_id: Union[str, int],
    action: str = "typing",
    thread: Optional[ThreadSpec] = None,
) -> None:
    params: Dict[str, Any] = {
        "chat_id": chat_id,
        "action": action,
        **build_thread_params(thread),
    }
    try:
        await bot.send_chat_action(**params)
    except Exception as exc:
        log(f"sendChatAction 失败: {exc}", "DEBUG")
