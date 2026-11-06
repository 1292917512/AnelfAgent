"""将本地出站媒体快照保存到上传目录，复用已鉴权的附件服务。

本模块只负责资源发布；会话记录仍由统一发送管道持久化。
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from agent.channel.utils.media import safe_filename
from core.config import get_config_int
from core.path import ConfigPaths, parse_upload_url, upload_url_for


def publish_media(path: str, media_type: str) -> str:
    """返回可服务 URL；本地文件复制成功后才发布，不依赖源文件后续生命周期。"""
    if path.startswith(("http://", "https://")):
        return path
    upload_root = Path(ConfigPaths.UPLOAD_DIR).resolve()
    uploaded = parse_upload_url(path)
    if path.startswith("/api/") and uploaded is None:
        raise ValueError("媒体 API 地址必须是上传接口返回的附件 URL")
    if uploaded is not None:
        source = (upload_root / uploaded[0] / uploaded[1]).resolve()
        if not source.is_relative_to(upload_root):
            raise ValueError("附件路径超出上传目录")
    else:
        if not path:
            raise ValueError("媒体路径不能为空")
        source = Path(path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"媒体文件不存在或不是普通文件: {path}")

    file_type = "audio" if media_type == "voice" else media_type
    if file_type not in {"image", "audio", "video", "file"}:
        raise ValueError(f"不支持的媒体类型: {media_type}")
    max_bytes = max(1, get_config_int("upload_max_mb", 50)) * 1024 * 1024
    directory = (upload_root / file_type).resolve()
    if not directory.is_relative_to(upload_root):
        raise ValueError("媒体目录超出上传目录")
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}_{safe_filename(source.name)}"
    destination = directory / filename
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            size = 0
            while chunk := reader.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError(f"媒体文件超过上传限制（{max_bytes // (1024 * 1024)}MB）")
                writer.write(chunk)
            if size == 0:
                raise ValueError("媒体文件为空")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return upload_url_for(file_type, filename)
