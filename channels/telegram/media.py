"""Telegram 媒体处理 -- 下载、上传、类型检测。

参照 openclaw resolveMedia。
"""

from __future__ import annotations

import mimetypes
import os
import tempfile
from typing import Any, Optional

from core.log import log

from .types import MediaResult

MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024  # Telegram Bot API 20MB 下载限制
MEDIA_TEMP_DIR = os.path.join(tempfile.gettempdir(), "anelf_tg_media")


def ensure_temp_dir() -> str:
    os.makedirs(MEDIA_TEMP_DIR, exist_ok=True)
    return MEDIA_TEMP_DIR


async def download_media(
    message: Any,
    context: Any,
    *,
    max_bytes: int = MAX_DOWNLOAD_SIZE,
) -> Optional[MediaResult]:
    """从 Telegram 消息中下载媒体文件。"""
    file_obj = _resolve_file_object(message)
    if not file_obj:
        return None

    file_id = getattr(file_obj, "file_id", None)
    if not file_id:
        return None

    file_size = getattr(file_obj, "file_size", None) or 0
    if file_size > max_bytes:
        log(f"Telegram 媒体文件过大: {file_size} bytes > {max_bytes}", "WARNING")
        return None

    try:
        tg_file = await context.bot.get_file(file_id)
        file_path = tg_file.file_path or ""
        if not file_path:
            log("Telegram getFile 无 file_path", "WARNING")
            return None

        temp_dir = ensure_temp_dir()
        ext = os.path.splitext(file_path)[-1] or _guess_extension(message)
        local_path = os.path.join(temp_dir, f"{file_id}{ext}")

        await tg_file.download_to_drive(local_path)

        mime, _ = mimetypes.guess_type(local_path)
        placeholder = _resolve_placeholder(message)
        file_name = _resolve_file_name(message, file_obj) or os.path.basename(local_path)

        return MediaResult(
            path=local_path,
            content_type=mime,
            placeholder=placeholder,
            file_name=file_name,
        )
    except Exception as exc:
        if "file is too big" in str(exc).lower():
            log("Telegram 文件超过 Bot API 20MB 限制", "WARNING")
            return None
        log(f"Telegram 媒体下载失败: {exc}", "WARNING")
        return None


def media_kind_from_mime(mime: Optional[str]) -> str:
    if not mime:
        return "document"
    if mime.startswith("image/gif"):
        return "animation"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    return "document"


# ------------------------------------------------------------------
# 内部辅助
# ------------------------------------------------------------------

def _resolve_file_object(message: Any) -> Any:
    if message.photo:
        return message.photo[-1]
    for attr in ("video", "video_note", "animation", "audio", "voice", "document", "sticker"):
        obj = getattr(message, attr, None)
        if obj:
            return obj
    return None


def _resolve_placeholder(message: Any) -> str:
    if message.photo:
        return "<media:photo>"
    if message.video or message.video_note:
        return "<media:video>"
    if message.animation:
        return "<media:animation>"
    if message.audio:
        return "<media:audio>"
    if message.voice:
        return "<media:voice>"
    if message.sticker:
        return "<media:sticker>"
    if message.document:
        return "<media:document>"
    return "<media:unknown>"


def _resolve_file_name(message: Any, file_obj: Any) -> str:
    name = getattr(file_obj, "file_name", None)
    if name:
        return name
    if message.audio:
        return getattr(message.audio, "title", None) or "audio"
    if message.document:
        return getattr(message.document, "file_name", None) or "document"
    return ""


def _guess_extension(message: Any) -> str:
    if message.photo:
        return ".jpg"
    if message.video or message.video_note:
        return ".mp4"
    if message.animation:
        return ".gif"
    if message.audio:
        return ".mp3"
    if message.voice:
        return ".ogg"
    if message.sticker:
        return ".webp"
    return ""
