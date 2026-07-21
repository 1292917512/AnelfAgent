"""通用媒体处理工具 — 跨频道复用。

借鉴 openclaw resolveMedia + 现有 channels/telegram/media.py + channels/qq/media.py
的通用部分，提供：

- 临时目录管理
- 文件下载（带大小限制）
- MIME 类型检测
- 文件扩展名推断
- 本地缓存清理

与频道特定的部分（如 telegram 的 file_id → file_path 解析）保留在各频道自己的 media.py。
"""

from __future__ import annotations

import mimetypes
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.log import log


# 全局媒体临时目录（所有频道共享）
MEDIA_TEMP_DIR = os.path.join(tempfile.gettempdir(), "anelf_media")
MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024  # 默认 20MB


@dataclass
class LocalMedia:
    """本地化后的媒体文件描述。"""

    path: str
    mime_type: Optional[str] = None
    file_name: str = ""
    size_bytes: int = 0


def ensure_temp_dir(subdir: str = "") -> str:
    """确保临时目录存在，返回路径。

    Args:
        subdir: 子目录名（如 "telegram" / "qq"），用于按频道隔离
    """
    path = os.path.join(MEDIA_TEMP_DIR, subdir) if subdir else MEDIA_TEMP_DIR
    os.makedirs(path, exist_ok=True)
    return path


def guess_mime_type(path: str) -> Optional[str]:
    """从文件路径猜测 MIME 类型。"""
    mime, _ = mimetypes.guess_type(path)
    return mime


def guess_extension_from_mime(mime: Optional[str]) -> str:
    """从 MIME 类型推断扩展名。"""
    if not mime:
        return ""
    ext = mimetypes.guess_extension(mime)
    return ext or ""


def media_kind_from_mime(mime: Optional[str]) -> str:
    """根据 MIME 推断媒体类别：image / video / audio / voice / document。"""
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


def is_within_size_limit(size_bytes: int, max_bytes: int = MAX_DOWNLOAD_SIZE) -> bool:
    """检查文件大小是否在限制内。"""
    return 0 < size_bytes <= max_bytes


def safe_filename(name: str, fallback: str = "file") -> str:
    """清理文件名，移除路径分隔符与控制字符。"""
    if not name:
        return fallback
    # 移除路径分隔符
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    # 移除控制字符
    name = "".join(c for c in name if ord(c) >= 32 or c in "\t\n")
    # 截断长度
    if len(name) > 128:
        stem, ext = os.path.splitext(name)
        name = stem[:120] + ext
    return name or fallback


def build_temp_path(file_id: str, ext: str = "", subdir: str = "") -> str:
    """构造临时文件路径。"""
    temp_dir = ensure_temp_dir(subdir)
    safe_id = safe_filename(file_id, fallback="media")
    return os.path.join(temp_dir, f"{safe_id}{ext}")


async def download_url_to_temp(
    url: str,
    *,
    subdir: str = "",
    max_bytes: int = MAX_DOWNLOAD_SIZE,
    timeout: float = 30.0,
) -> Optional[LocalMedia]:
    """从 URL 下载到本地临时目录。

    Returns:
        LocalMedia（成功）或 None（失败/超限）。
    """
    try:
        import httpx
    except ImportError:
        log("httpx 未安装，无法下载 URL", "WARNING", tag="媒体")
        return None

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    log(f"下载失败: HTTP {resp.status_code} {url}", "WARNING", tag="媒体")
                    return None

                content_length = resp.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    log(
                        f"文件过大: {content_length} > {max_bytes} {url}",
                        "WARNING",
                        tag="媒体",
                    )
                    return None

                # 推断文件名与扩展名
                content_type = resp.headers.get("content-type", "")
                ext = guess_extension_from_mime(content_type)
                # 从 URL 兜底
                if not ext:
                    url_path = url.split("?")[0]
                    ext = os.path.splitext(url_path)[1]

                file_id = url.split("/")[-1].split("?")[0] or "download"
                local_path = build_temp_path(file_id, ext, subdir)

                # 流式写入
                size = 0
                with open(local_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            f.close()
                            os.unlink(local_path)
                            log(f"下载超限: {url}", "WARNING", tag="媒体")
                            return None
                        f.write(chunk)

                return LocalMedia(
                    path=local_path,
                    mime_type=content_type or guess_mime_type(local_path),
                    file_name=os.path.basename(local_path),
                    size_bytes=size,
                )
    except Exception as exc:
        log(f"下载异常: {url}: {exc}", "WARNING", tag="媒体")
        return None


def cleanup_old_files(max_age_seconds: float = 3600.0, subdir: str = "") -> int:
    """清理临时目录中超过 max_age 的文件。返回清理数量。"""
    import time
    temp_dir = ensure_temp_dir(subdir)
    now = time.time()
    removed = 0
    try:
        for entry in os.listdir(temp_dir):
            path = os.path.join(temp_dir, entry)
            if not os.path.isfile(path):
                continue
            age = now - os.path.getmtime(path)
            if age > max_age_seconds:
                try:
                    os.unlink(path)
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return removed
