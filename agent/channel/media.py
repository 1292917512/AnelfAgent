"""媒体文件下载工具 — 将远程 URL 按需下载到本地 workspace。

频道媒体不再入站时自动下载，而是由 AI 通过工具（web_download /
qq_download_file）按需触发，此处提供统一的落盘实现。
"""

from __future__ import annotations

import os
import time
import uuid

import httpx

from core.log import log

from .schemas import SegmentType

MAX_DOWNLOAD_SIZE = 20 * 1024 * 1024  # 20 MB
DOWNLOAD_TIMEOUT = 30.0

_TYPE_DIR_MAP = {
    SegmentType.IMAGE: "image",
    SegmentType.VOICE: "voice",
    SegmentType.AUDIO: "audio",
    SegmentType.VIDEO: "video",
    SegmentType.FILE: "file",
}

_TYPE_EXT_MAP = {
    SegmentType.IMAGE: ".jpg",
    SegmentType.VOICE: ".ogg",
    SegmentType.AUDIO: ".mp3",
    SegmentType.VIDEO: ".mp4",
    SegmentType.FILE: ".bin",
}


def get_upload_dir() -> str:
    """返回 workspace 下的 uploads 根目录（绝对路径）。"""
    try:
        from core.config import ConfigManager
        ws = ConfigManager.get("workspace_root", "workspace")
    except Exception:
        ws = "workspace"
    return os.path.abspath(os.path.join(ws, "uploads"))


def _guess_ext(url: str, seg_type: SegmentType) -> str:
    """从 URL 尾部猜测扩展名，猜不到则用默认值。"""
    path_part = url.split("?")[0].split("#")[0]
    if "." in os.path.basename(path_part):
        ext = "." + path_part.rsplit(".", 1)[-1].lower()
        if 2 <= len(ext) <= 6:
            return ext
    return _TYPE_EXT_MAP.get(seg_type, ".bin")


async def download_to_uploads(
    url: str,
    seg_type: SegmentType,
    save_name: str = "",
    max_size: int = MAX_DOWNLOAD_SIZE,
) -> str:
    """下载单个 URL 到 uploads 对应子目录，返回本地路径。失败返回空字符串。

    Args:
        url: 远程文件地址（http/https）
        seg_type: 媒体类型，决定落盘子目录与默认扩展名
        save_name: 期望的文件名（仅取 basename，附加唯一前缀防冲突）
        max_size: 允许的最大字节数，超限跳过
    """
    sub_dir = _TYPE_DIR_MAP.get(seg_type, "file")
    dl_dir = os.path.join(get_upload_dir(), sub_dir)
    os.makedirs(dl_dir, exist_ok=True)

    short_id = uuid.uuid4().hex[:8]
    if save_name:
        base = os.path.basename(save_name).strip() or f"file{_guess_ext(url, seg_type)}"
        filename = f"{int(time.time() * 1000)}_{short_id}_{base}"
    else:
        ext = _guess_ext(url, seg_type)
        filename = f"{int(time.time() * 1000)}_{short_id}{ext}"
    local_path = os.path.join(dl_dir, filename)

    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            content_length = int(resp.headers.get("content-length", 0))
            if content_length > max_size:
                log(f"媒体下载跳过（超过 {max_size // 1024 // 1024}MB）: {url}", "WARNING")
                return ""

            data = resp.content
            if len(data) > max_size:
                log(f"媒体下载跳过（超过 {max_size // 1024 // 1024}MB）: {url}", "WARNING")
                return ""

            with open(local_path, "wb") as f:
                f.write(data)

        log(f"媒体已下载: {seg_type.value} -> {local_path} ({len(data)} bytes)")
        return local_path

    except Exception as exc:
        log(f"媒体下载失败: {url} -> {exc}", "WARNING")
        return ""
