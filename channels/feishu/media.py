"""飞书媒体处理 -- 图片/文件/音视频的上传与下载。"""

from __future__ import annotations

import asyncio
import io
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

from .types import FeishuMediaInfo

_UPLOAD_DIR = os.path.join("workspace", "uploads")
_MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


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
        return resp.data.image_key  # type: ignore[union-attr]

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
        return resp.data.file_key  # type: ignore[union-attr]

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


def _infer_ext(msg_type: str, content_type: str) -> str:
    if content_type:
        mapping = {
            "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
            "image/webp": "webp", "audio/ogg": "ogg", "audio/mp3": "mp3",
            "audio/mpeg": "mp3", "video/mp4": "mp4", "application/pdf": "pdf",
        }
        for k, v in mapping.items():
            if k in content_type:
                return v
    return {"image": "png", "audio": "ogg", "video": "mp4", "sticker": "png"}.get(msg_type, "bin")


async def download_message_resource(
    client: lark.Client,
    message_id: str,
    file_key: str,
    resource_type: str = "file",
    msg_type: str = "file",
    file_name: str = "",
) -> FeishuMediaInfo:
    """通过 messageResource API 下载消息中的媒体文件。"""

    def _do() -> FeishuMediaInfo:
        req = GetMessageResourceRequest.builder() \
            .message_id(message_id) \
            .file_key(file_key) \
            .type(resource_type) \
            .build()
        resp = client.im.v1.message_resource.get(req)
        if not resp.success():
            raise RuntimeError(f"飞书资源下载失败: code={resp.code}, msg={resp.msg}")

        subdir = _infer_subdir(msg_type)
        save_dir = os.path.abspath(os.path.join(_UPLOAD_DIR, subdir))
        os.makedirs(save_dir, exist_ok=True)

        content_type = ""
        raw_file = resp.file
        if raw_file is None:
            raise RuntimeError("飞书资源下载失败: 响应中无文件数据")

        # 读取字节
        if hasattr(raw_file, "read"):
            data = raw_file.read()
        elif isinstance(raw_file, (bytes, bytearray)):
            data = bytes(raw_file)
        else:
            data = bytes(raw_file)

        if len(data) > _MAX_FILE_SIZE:
            raise RuntimeError(f"文件过大 ({len(data)} bytes)，超出 {_MAX_FILE_SIZE} 限制")

        ext = _infer_ext(msg_type, content_type)
        name = file_name or f"{int(time.time() * 1000)}_{file_key[:12]}.{ext}"
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
            content_type=content_type,
            placeholder=placeholder,
            file_name=name,
        )

    return await asyncio.to_thread(_do)


async def download_image(
    client: lark.Client,
    message_id: str,
    image_key: str,
) -> Optional[FeishuMediaInfo]:
    """下载消息中的图片。"""
    try:
        return await download_message_resource(
            client, message_id, image_key,
            resource_type="image", msg_type="image",
        )
    except Exception as exc:
        log(f"飞书图片下载失败 ({image_key}): {exc}", "WARNING")
        return None
