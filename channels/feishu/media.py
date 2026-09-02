"""飞书媒体处理 -- 图片/文件/音视频的上传与下载。"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    GetMessageResourceRequest,
)

from core.log import log
from core.path import ConfigPaths

from .errors import raise_for_fail
from .types import FeishuMediaInfo

_UPLOAD_DIR = ConfigPaths.UPLOAD_DIR
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB（adapter 层经 max_download_mb 配置覆盖）


# ------------------------------------------------------------------
# 上传
# ------------------------------------------------------------------


async def upload_image(client: lark.Client, image_path: str) -> str:
    """上传图片到飞书，返回 image_key。"""
    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"图片文件不存在: {image_path}")

    def _do() -> str:
        with open(image_path, "rb") as f:
            req = CreateImageRequest.builder() \
                .request_body(
                    CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(f)
                    .build()
                ).build()
            resp = client.im.v1.image.create(req)
        if not resp.success():
            raise RuntimeError(f"飞书图片上传失败: code={resp.code}, msg={resp.msg}")
        return resp.data.image_key or ""

    return await asyncio.to_thread(_do)


async def upload_file(
    client: lark.Client,
    file_path: str,
    file_type: str = "stream",
    file_name: str = "",
) -> str:
    """上传文件到飞书，返回 file_key。

    Args:
        file_type: opus/mp4/pdf/doc/xls/ppt/stream 等
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    name = file_name or os.path.basename(file_path)

    def _do() -> str:
        with open(file_path, "rb") as f:
            req = CreateFileRequest.builder() \
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type(file_type)
                    .file_name(name)
                    .file(f)
                    .build()
                ).build()
            resp = client.im.v1.file.create(req)
        if not resp.success():
            raise RuntimeError(f"飞书文件上传失败: code={resp.code}, msg={resp.msg}")
        return resp.data.file_key or ""

    return await asyncio.to_thread(_do)


# ------------------------------------------------------------------
# 下载
# ------------------------------------------------------------------


def _infer_subdir(msg_type: str) -> str:
    return {
        "image": "image",
        "audio": "audio",
        "video": "video",
        "media": "video",
        "file": "file",
        "sticker": "image",
    }.get(msg_type, "file")


# 常见媒体魔数 → 扩展名（file_name 缺失时的内容级推断）
_MAGIC_EXT = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"RIFF", "webp"),          # RIFF....WEBP 需二次确认，见 _sniff_ext
    (b"%PDF", "pdf"),
    (b"OggS", "ogg"),
    (b"ID3", "mp3"),
    (b"\x1aE\xdf\xa3", "webm"),
]


def _sniff_ext(data: bytes) -> str:
    """按文件头魔数嗅探扩展名（识别不出返回空串）。"""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "mp4"  # ISO BMFF（mp4/mov 统一按 mp4 处理）
    for magic, ext in _MAGIC_EXT:
        if data.startswith(magic):
            if ext == "webp" and data[8:12] != b"WEBP":
                continue
            return ext
    return ""


def _resolve_ext(msg_type: str, file_name: str, data: bytes) -> str:
    """扩展名解析链：file_name 后缀 → 魔数嗅探 → 消息类型兜底。"""
    if file_name and "." in file_name:
        ext = file_name.rsplit(".", 1)[-1].lower()
        if ext and ext.isalnum() and len(ext) <= 8:
            return ext
    sniffed = _sniff_ext(data)
    if sniffed:
        return sniffed
    return {"image": "png", "audio": "ogg", "video": "mp4", "media": "mp4", "sticker": "png"}.get(msg_type, "bin")


async def download_message_resource(
    client: lark.Client,
    message_id: str,
    file_key: str,
    resource_type: str = "file",
    msg_type: str = "file",
    file_name: str = "",
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> FeishuMediaInfo:
    """通过 messageResource API 下载消息中的媒体文件。"""

    def _do() -> FeishuMediaInfo:
        req = GetMessageResourceRequest.builder() \
            .message_id(message_id) \
            .file_key(file_key) \
            .type(resource_type) \
            .build()
        resp = client.im.v1.message_resource.get(req)
        raise_for_fail(resp, "媒体下载")

        subdir = _infer_subdir(msg_type)
        save_dir = os.path.abspath(os.path.join(_UPLOAD_DIR, subdir))
        os.makedirs(save_dir, exist_ok=True)

        raw_file = resp.file
        if raw_file is None:
            raise RuntimeError("飞书媒体下载失败: 响应中无文件数据")

        # 读取字节
        if hasattr(raw_file, "read"):
            data = raw_file.read()
        else:
            data = bytes(raw_file)

        if len(data) > max_bytes:
            raise RuntimeError(
                f"飞书媒体下载失败: 文件 {len(data)} 字节超出上限 {max_bytes} 字节"
                f"（可通过频道配置 max_download_mb 调整）"
            )

        # SDK 会从 Content-Disposition 解析出服务端文件名，优先采用
        resolved_name = file_name or getattr(resp, "file_name", "") or ""
        ext = _resolve_ext(msg_type, resolved_name, data)
        name = resolved_name or f"{int(time.time() * 1000)}_{file_key[:12]}.{ext}"
        if "." not in name:
            name = f"{name}.{ext}"
        local_path = os.path.join(save_dir, name)
        with open(local_path, "wb") as f:
            f.write(data)

        placeholder = {
            "image": "<media:image>",
            "audio": "<media:audio>",
            "video": "<media:video>",
            "media": "<media:video>",
            "sticker": "<media:sticker>",
        }.get(msg_type, "<media:document>")

        return FeishuMediaInfo(
            path=local_path,
            content_type="",
            placeholder=placeholder,
            file_name=name,
        )

    return await asyncio.to_thread(_do)


async def download_image(
    client: lark.Client,
    message_id: str,
    image_key: str,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> Optional[FeishuMediaInfo]:
    """下载消息中的图片。"""
    try:
        return await download_message_resource(
            client, message_id, image_key,
            resource_type="image", msg_type="image",
            max_bytes=max_bytes,
        )
    except Exception as exc:
        log(f"飞书图片下载失败 ({image_key}): {exc}", "WARNING")
        return None
