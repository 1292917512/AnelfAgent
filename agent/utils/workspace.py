"""工作区路径解析与媒体产物落盘 — 核心层共享工具。

路径解析经 agent.approval.policy.workspace_paths_port 晚绑定端口获取
与文件工具一致的工作区解析/沙箱判定（端口未施绑时仅接受绝对路径）。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
from typing import List


def get_workspace_root() -> str:
    """工作区根目录（端口未施绑时回退配置值）。"""
    from agent.approval.policy import workspace_paths_port
    if workspace_paths_port.bound:
        return workspace_paths_port.get().get_root()
    try:
        from core.config import ConfigManager
        return str(ConfigManager.get("workspace_root", "workspace"))
    except Exception:
        return "workspace"


def resolve_workspace_path(path: str) -> str:
    """解析工具入参路径为绝对路径，越出工作区沙箱时抛 ValueError。

    沙箱开启时（含绝对路径）统一经端口判定；沙箱关闭/端口未施绑时
    绝对路径直用，相对路径基于工作区根解析。
    """
    if not path:
        return ""
    from agent.approval.policy import workspace_paths_port
    if workspace_paths_port.bound:
        fns = workspace_paths_port.get()
        resolved = fns.resolve(path)
        if not fns.allowed(resolved):
            raise ValueError(f"沙箱限制: {path} 不在工作目录内")
        return resolved
    if os.path.isabs(path):
        return os.path.normpath(path)
    raise ValueError("相对路径需要 workspace 解析（请传绝对路径）")


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


def save_audio(audio_bytes: bytes, fmt: str = "mp3", prefix: str = "gen", kind: str = "audio") -> str:
    """保存音频到 workspace/uploads/<kind>/，返回相对路径。"""
    fname = f"{prefix}_{int(time.time() * 1000)}.{fmt}"
    fpath = os.path.join(_upload_dir(kind), fname)
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
