"""媒体库共享工具：workspace 路径解析（沙箱校验）、图片输入归一化、产物落盘。"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from typing import List


def get_workspace_root() -> str:
    try:
        from core.config import ConfigManager
        return ConfigManager.get("workspace_root", "workspace")
    except Exception:
        return "workspace"


def resolve_workspace_path(path: str) -> str:
    """解析可能相对于 workspace 或 CWD 的路径。

    沙箱开启时（含绝对路径）统一经 entities/filesystem/paths.py 解析并做沙箱校验，
    越界时抛 ValueError；沙箱关闭时保持原有解析行为。
    """
    if not path:
        return ""
    from entities.filesystem import paths as _paths
    if _paths.sandbox_enabled():
        ws_abs = os.path.abspath(get_workspace_root())
        resolved = _paths.resolve_workspace_path(path, ws_abs)
        if not _paths.check_sandbox(resolved, ws_abs):
            raise ValueError(f"沙箱限制: {path} 不在工作目录内")
        return resolved
    if os.path.isabs(path):
        return path
    ws_root = get_workspace_root()
    ws_abs = os.path.abspath(ws_root)
    candidate = os.path.join(os.getcwd(), path)
    if os.path.exists(candidate):
        return candidate
    ws = os.path.join(ws_abs, path)
    if os.path.exists(ws):
        return ws
    norm = os.path.normpath(path)
    ws_norm = os.path.normpath(ws_root)
    if norm.startswith(ws_norm + os.sep):
        stripped = norm[len(ws_norm + os.sep):]
        ws2 = os.path.join(ws_abs, stripped)
        if os.path.exists(ws2):
            return ws2
    return candidate


def to_image_value(path_or_url: str) -> str:
    """将图片输入规范化为 URL 或 data:base64；本地路径做沙箱校验并读文件转码。"""
    if path_or_url.startswith(("http://", "https://", "data:image/", "mm_file://")):
        return path_or_url
    resolved = resolve_workspace_path(path_or_url)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"图片不存在: {path_or_url}")
    mime_type = mimetypes.guess_type(os.path.basename(resolved))[0] or "image/png"
    with open(resolved, "rb") as f:
        raw = f.read()
    return f"data:{mime_type};base64,{base64.b64encode(raw).decode()}"


def parse_subject_reference(value: str) -> List[str]:
    """解析主体参考图片参数：支持单个路径/URL 或 JSON 字符串数组。"""
    value = value.strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            items = json.loads(value)
            if isinstance(items, list):
                return [str(item) for item in items if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [value]


def _upload_dir(kind: str) -> str:
    save_dir = os.path.join(os.path.abspath(get_workspace_root()), "uploads", kind)
    os.makedirs(save_dir, exist_ok=True)
    return save_dir


def _rel(path: str) -> str:
    return os.path.relpath(path, os.getcwd()).replace("\\", "/")


def save_audio(audio_bytes: bytes, fmt: str = "mp3", prefix: str = "gen") -> str:
    """保存音频到 workspace/uploads/audio/，返回相对路径。"""
    fname = f"{prefix}_{int(time.time() * 1000)}.{fmt}"
    fpath = os.path.join(_upload_dir("audio"), fname)
    with open(fpath, "wb") as f:
        f.write(audio_bytes)
    return _rel(fpath)


async def save_images(image_results: List[str], prefix: str = "gen") -> List[str]:
    """下载/解码图片结果（URL 或 data:base64）保存到 workspace/uploads/image/。"""
    import httpx

    saved: List[str] = []
    for i, src in enumerate(image_results):
        if src.startswith("data:image/"):
            header, b64 = src.split(",", 1)
            img_bytes = base64.b64decode(b64)
            ext = ".png" if "png" in header else ".jpg"
        else:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(src, follow_redirects=True)
                resp.raise_for_status()
                img_bytes = resp.content
                ct = resp.headers.get("content-type", "image/png")
                ext = ".png" if "png" in ct else ".jpg"
        fname = f"{prefix}_{int(time.time() * 1000)}_{i}{ext}"
        fpath = os.path.join(_upload_dir("image"), fname)
        with open(fpath, "wb") as f:
            f.write(img_bytes)
        saved.append(_rel(fpath))
    return saved


async def save_video(video_url: str, prefix: str = "gen") -> str:
    """下载视频保存到 workspace/uploads/video/，返回相对路径。"""
    import httpx

    async with httpx.AsyncClient(timeout=300.0) as client:
        resp = await client.get(video_url, follow_redirects=True)
        resp.raise_for_status()
        data = resp.content
    fname = f"{prefix}_{int(time.time() * 1000)}.mp4"
    fpath = os.path.join(_upload_dir("video"), fname)
    with open(fpath, "wb") as f:
        f.write(data)
    return _rel(fpath)
