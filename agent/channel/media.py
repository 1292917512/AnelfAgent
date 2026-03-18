"""媒体文件下载工具 — 统一将远程 URL 下载到本地 workspace。

频道适配器只需在 MessageSegment 中填入 url，
核心层在 dispatch_inbound 前调用 ensure_local_media 自动下载。
"""

from __future__ import annotations

import os
import time
import uuid
from typing import List

import httpx

from core.log import log

from .schemas import MessageSegment, SegmentType

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


def _get_upload_dir() -> str:
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


async def ensure_local_media(segments: List[MessageSegment]) -> List[MessageSegment]:
    """遍历消息段，将有远程 URL 但无本地路径的媒体下载到 workspace/uploads/。

    已有 file_path（且文件存在）的 segment 不重复下载。
    TEXT / AT / LOCATION 等无需下载的类型直接跳过。
    返回更新后的 segments 列表（原地修改）。
    """
    downloadable = {SegmentType.IMAGE, SegmentType.VOICE, SegmentType.AUDIO,
                    SegmentType.VIDEO, SegmentType.FILE}

    for seg in segments:
        if seg.type not in downloadable:
            continue

        if seg.file_path and os.path.exists(seg.file_path):
            continue

        url = seg.url
        if not url or not url.startswith(("http://", "https://")):
            continue

        local_path = await _download_to_local(url, seg.type)
        if local_path:
            seg.file_path = local_path
            if not seg.file_name:
                seg.file_name = os.path.basename(local_path)

    return segments


async def _download_to_local(url: str, seg_type: SegmentType) -> str:
    """下载单个 URL 到本地，返回本地路径。失败返回空字符串。"""
    sub_dir = _TYPE_DIR_MAP.get(seg_type, "file")
    dl_dir = os.path.join(_get_upload_dir(), sub_dir)
    os.makedirs(dl_dir, exist_ok=True)

    ext = _guess_ext(url, seg_type)
    short_id = uuid.uuid4().hex[:8]
    filename = f"{int(time.time() * 1000)}_{short_id}{ext}"
    local_path = os.path.join(dl_dir, filename)

    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            content_length = int(resp.headers.get("content-length", 0))
            if content_length > MAX_DOWNLOAD_SIZE:
                log(f"媒体下载跳过（超过 {MAX_DOWNLOAD_SIZE // 1024 // 1024}MB）: {url}", "WARNING")
                return ""

            data = resp.content
            if len(data) > MAX_DOWNLOAD_SIZE:
                log(f"媒体下载跳过（超过 {MAX_DOWNLOAD_SIZE // 1024 // 1024}MB）: {url}", "WARNING")
                return ""

            with open(local_path, "wb") as f:
                f.write(data)

        log(f"媒体已下载: {seg_type.value} -> {local_path} ({len(data)} bytes)")
        return local_path

    except Exception as exc:
        log(f"媒体下载失败: {url} -> {exc}", "WARNING")
        return ""
