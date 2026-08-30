"""图片处理工具：加载、压缩、格式转换。"""

from __future__ import annotations

import asyncio
import base64
import io
import mimetypes
from pathlib import Path
from typing import List, Optional

from agent.llm.types import ImageContent, VideoContent

_MAX_LONG_EDGE = 1568
_MAX_IMAGE_KB = 1024


def _resolve_image_path(path: str) -> Path:
    """解析图片路径：相对路径优先按当前目录，其次按工作区根目录解析。"""
    p = Path(path)
    if p.is_absolute() or p.exists():
        return p
    try:
        from core.config import ConfigManager
        workspace_root = ConfigManager.get("workspace_root", "workspace")
    except Exception:
        workspace_root = "workspace"
    candidate = Path(workspace_root) / path
    if candidate.exists():
        return candidate
    return p


def load_image_from_path(path: str | Path) -> ImageContent:
    """从文件路径加载图片并转为 base64 ImageContent（相对路径自动按工作区解析）。"""
    p = _resolve_image_path(str(path))
    if not p.exists():
        raise FileNotFoundError(f"图片路径不存在: {p}")
    if not p.is_file():
        raise IsADirectoryError(f"路径不是文件: {p}")
    if p.stat().st_size > _MAX_IMAGE_DOWNLOAD_BYTES:
        raise ValueError(f"图片超过体积上限（{_MAX_IMAGE_DOWNLOAD_BYTES // 1024 // 1024}MB）: {p}")

    mime_type, _ = mimetypes.guess_type(str(p))
    if not mime_type or not mime_type.startswith("image/"):
        mime_type = "image/jpeg"

    data = base64.b64encode(p.read_bytes()).decode("utf-8")
    return ImageContent(data=data, mime_type=mime_type)


def load_image_from_bytes(
    raw: bytes, mime_type: str = "image/jpeg",
) -> ImageContent:
    """从字节数据创建 ImageContent。"""
    data = base64.b64encode(raw).decode("utf-8")
    return ImageContent(data=data, mime_type=mime_type)


# 下载/读取的体积上限（防异常 URL 或超大文件一次性吃满内存；
# 压缩在读取之后，上限必须在读取之前）
_MAX_IMAGE_DOWNLOAD_BYTES = 32 * 1024 * 1024   # 图片 32MB
_MAX_VIDEO_DOWNLOAD_BYTES = 256 * 1024 * 1024  # 视频 256MB


async def download_image_to_base64(url: str, timeout: float = 30.0) -> Optional[ImageContent]:
    """下载 URL 图片并转为 base64 ImageContent（超体积上限放弃）。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            if len(resp.content) > _MAX_IMAGE_DOWNLOAD_BYTES:
                raise ValueError(f"图片超过体积上限（{_MAX_IMAGE_DOWNLOAD_BYTES // 1024 // 1024}MB）")
            content_type = resp.headers.get("content-type", "image/jpeg")
            mime = content_type.split(";")[0].strip()
            if not mime.startswith("image/"):
                mime = "image/jpeg"
            data = base64.b64encode(resp.content).decode("utf-8")
            return ImageContent(data=data, mime_type=mime)
    except Exception as e:
        from core.log import log as _log
        _log(f"图片下载失败 ({url[:80]}): {e}", "DEBUG", tag="媒体")
        return None


# 常见图片格式的 base64 魔数前缀（避免把短 base64 误判为文件路径）
_BASE64_MAGIC_PREFIXES = ("/9j/", "iVBOR", "R0lGOD", "UklGR", "Qk2", "SUkq")

# 常见视频扩展名（用于把视频文件从图片识别链路分流到视频识别链路）
_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".flv", ".wmv", ".3gp"}


def is_video_path(path: str) -> bool:
    """判断路径或 URL 是否指向视频文件（按扩展名/路径后缀识别）。"""
    clean = str(path).split("?", 1)[0].split("#", 1)[0]
    return Path(clean).suffix.lower() in _VIDEO_EXTENSIONS


def load_video_from_path(path: str | Path) -> VideoContent:
    """从文件路径加载视频并转为 base64 VideoContent（相对路径自动按工作区解析）。"""
    p = _resolve_image_path(str(path))
    if not p.exists():
        raise FileNotFoundError(f"视频路径不存在: {p}")
    if not p.is_file():
        raise IsADirectoryError(f"路径不是文件: {p}")

    mime_type, _ = mimetypes.guess_type(str(p))
    if not mime_type or not mime_type.startswith("video/"):
        mime_type = "video/mp4"

    data = base64.b64encode(p.read_bytes()).decode("utf-8")
    return VideoContent(data=data, mime_type=mime_type)


async def download_video_to_base64(url: str, timeout: float = 120.0) -> Optional[VideoContent]:
    """下载 URL 视频并转为 base64 VideoContent。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            mime = content_type.split(";")[0].strip()
            if not mime.startswith("video/"):
                guessed, _ = mimetypes.guess_type(url.split("?", 1)[0])
                mime = guessed if guessed and guessed.startswith("video/") else "video/mp4"
            data = base64.b64encode(resp.content).decode("utf-8")
            return VideoContent(data=data, mime_type=mime)
    except Exception as e:
        from core.log import log as _log
        _log(f"视频下载失败 ({url[:80]}): {e}", "DEBUG", tag="媒体")
        return None


def _looks_like_file_path(data: str) -> bool:
    """Detect if data string is a local file path rather than base64."""
    if data.startswith(_BASE64_MAGIC_PREFIXES) or data.startswith("data:"):
        return False
    if "/" not in data and "\\" not in data:
        return False
    # 真实存在的路径优先（长度不设限：长路径也是路径）；
    # 不存在时按 base64 特征二次判断，
    # 避免不含魔数前缀的短 base64（小图标等）被误当路径丢弃
    try:
        if Path(data).exists():
            return True
        base64.b64decode(data, validate=True)
        return False
    except Exception:
        return True


async def ensure_base64(images: List[ImageContent]) -> List[ImageContent]:
    """确保所有图片都是 base64 格式，并自动压缩优化。

    自动处理三种来源：
    - URL → 下载并编码（失败保留原 URL，交由端点拉取）
    - 本地文件路径 → 读取并编码（失败则丢弃该图片，避免脏数据注入 LLM）
    - 已是 base64 → 直接使用

    每张图片加载后自动经过 optimize_for_vision 压缩。
    """
    prepared, _url_fallbacks, _failed = await ensure_base64_report(images)
    return prepared


# 单张图片的处理状态
PREPARED_OK = "ok"                    # 已归一为压缩后的 base64
PREPARED_URL_FALLBACK = "url_fallback"  # URL 下载失败，保留原 URL 交由端点兜底
PREPARED_FAILED = "failed"            # 本地路径加载失败，图片被丢弃


async def _prepare_one(img: ImageContent) -> "tuple[Optional[ImageContent], str]":
    """处理单张图片，返回 (图片, 状态)；失败状态返回 (None, PREPARED_FAILED)。"""
    from core.log import log as _log

    if img.is_url:
        converted = await download_image_to_base64(img.data)
        if converted is None:
            _log(f"ensure_base64: URL 下载失败，保留原 URL 兜底 ({img.data[:80]})",
                 "DEBUG", tag="媒体")
            return img, PREPARED_URL_FALLBACK
        loaded = converted
    elif _looks_like_file_path(img.data):
        try:
            loaded = await asyncio.to_thread(load_image_from_path, img.data)
        except Exception as exc:
            _log(f"ensure_base64: 图片加载失败，已丢弃 ({img.data}): {exc}",
                 "WARNING", tag="媒体")
            return None, PREPARED_FAILED
    else:
        loaded = img
    # PIL 解码/缩放/重编码是 CPU 密集操作，放工作线程不阻塞事件循环
    return await asyncio.to_thread(optimize_for_vision, loaded), PREPARED_OK


async def ensure_base64_report(
    images: List[ImageContent],
) -> "tuple[List[ImageContent], List[str], List[str]]":
    """ensure_base64 的状态报告版：并行处理，返回 (成功图片, URL 回退列表, 失败列表)。

    URL 回退与彻底失败分开报告：回退图片仍注入上下文（端点可能抓得到），
    但调用方应告知 Agent 该图未经本地验证——端点同样抓不到时模型看到的
    是坏图，不知情会产生幻觉描述。
    """
    results = await asyncio.gather(*(_prepare_one(img) for img in images))
    prepared: List[ImageContent] = []
    url_fallbacks: List[str] = []
    failed: List[str] = []
    for img, (loaded, status) in zip(images, results, strict=True):
        if status == PREPARED_OK and loaded is not None:
            prepared.append(loaded)
        elif status == PREPARED_URL_FALLBACK and loaded is not None:
            prepared.append(loaded)
            url_fallbacks.append(img.data[:80])
        else:
            failed.append(img.data[:80])
    return prepared, url_fallbacks, failed


def optimize_for_vision(
    image: ImageContent,
    *,
    max_long_edge: int = _MAX_LONG_EDGE,
    max_kb: int = _MAX_IMAGE_KB,
) -> ImageContent:
    """对发送给 LLM 的图片进行分辨率和体积优化。

    策略：先限制分辨率（效果最显著），再递减 JPEG 质量（保底）。
    1568px 长边是主流视觉模型的最佳分辨率上限（Claude 官方推荐值，
    OpenAI 在此分辨率下 tile 数合理，MiniMax 在支持范围内）。

    Args:
        max_long_edge: 最长边像素上限，超过则等比缩放
        max_kb: 体积上限（KB），超过则降低 JPEG 质量
    """
    if image.is_url or image.mime_type == "image/gif":
        return image

    try:
        raw = base64.b64decode(image.data)
    except Exception:
        return image

    try:
        from PIL import Image as PILImage
    except ImportError:
        return image

    try:
        img: "PILImage.Image" = PILImage.open(io.BytesIO(raw))
        # PIL 是惰性解码：截断文件/DecompressionBomb 等错误在 size/resize/save
        # 才抛出，整个处理段都必须在 try 内——单张坏图不能炸穿整条回复链
        src_format = (img.format or "").upper()
        # 动图（GIF/动态PNG/动态WEBP）不重编码，保持原样
        if getattr(img, "is_animated", False) and src_format in ("GIF", "PNG", "WEBP"):
            return image

        w, h = img.size
        original_bytes = len(raw)
        max_bytes = max_kb * 1024
        needs_resize = max(w, h) > max_long_edge
        needs_compress = original_bytes > max_bytes
        # 视觉 API 不支持的容器格式（如 QQ 图片常见的 MPO）统一取首帧重编码为 JPEG
        needs_reencode = src_format not in ("JPEG", "PNG", "GIF", "WEBP")

        if not needs_resize and not needs_compress and not needs_reencode:
            return image

        if needs_resize:
            scale = max_long_edge / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), PILImage.Resampling.LANCZOS)

        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")

        compressed = b""
        for quality in (85, 70, 55, 40):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            compressed = buf.getvalue()
            if len(compressed) <= max_bytes:
                break
    except Exception as exc:
        from core.log import log as _log
        _log(f"图片优化失败，按原样使用: {exc}", "DEBUG", tag="媒体")
        return image

    from core.log import log as _log
    _log(
        f"图片优化: {src_format} {w}x{h} ({original_bytes // 1024}KB) → "
        f"JPEG {img.size[0]}x{img.size[1]} ({len(compressed) // 1024}KB)",
        "DEBUG", tag="媒体",
    )
    return ImageContent(data=base64.b64encode(compressed).decode("utf-8"), mime_type="image/jpeg")


def build_multimodal_content(
    text: str,
    images: List[ImageContent],
    *,
    flat_url: bool = False,
) -> list[dict]:
    """将文本和图片列表构建为多模态 content 数组。

    每张图片自动经过 optimize_for_vision 压缩后再编码。

    Args:
        flat_url: 为 True 时使用 Ollama 兼容的扁平 image_url 格式。
    """
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for img in images:
        parts.append(optimize_for_vision(img).to_openai_block(flat_url=flat_url))
    return parts
