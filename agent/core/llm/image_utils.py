"""图片处理工具：加载、压缩、格式转换。"""

from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from typing import List, Optional

from agent.core.llm.types import ImageContent

_MAX_IMAGE_KB = 1024
_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def load_image_from_path(path: str | Path) -> ImageContent:
    """从文件路径加载图片并转为 base64 ImageContent。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"图片路径不存在: {p}")
    if not p.is_file():
        raise IsADirectoryError(f"路径不是文件: {p}")

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


def load_image_from_url(url: str) -> ImageContent:
    """从 URL 创建 ImageContent（不下载，直接引用）。"""
    return ImageContent(data=url, is_url=True)


async def download_image_to_base64(url: str, timeout: float = 30.0) -> Optional[ImageContent]:
    """下载 URL 图片并转为 base64 ImageContent。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
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


def _looks_like_file_path(data: str) -> bool:
    """Detect if data string is a local file path rather than base64."""
    return (len(data) < 500
            and ("/" in data or "\\" in data)
            and not data.startswith("data:"))


async def ensure_base64(images: List[ImageContent]) -> List[ImageContent]:
    """确保所有图片都是 base64 格式。

    自动处理三种来源：
    - URL → 下载并编码
    - 本地文件路径 → 读取并编码
    - 已是 base64 → 直接使用
    """
    from core.log import log as _log

    result: List[ImageContent] = []
    for img in images:
        if img.is_url:
            _log(f"ensure_base64: downloading URL ({img.data[:80]})", "DEBUG", tag="媒体")
            converted = await download_image_to_base64(img.data)
            result.append(converted if converted else img)
        elif _looks_like_file_path(img.data):
            _log(f"ensure_base64: loading local file ({img.data})", "DEBUG", tag="媒体")
            try:
                loaded = load_image_from_path(img.data)
                _log(f"ensure_base64: loaded OK, base64 len={len(loaded.data)}", "DEBUG", tag="媒体")
                result.append(loaded)
            except Exception as exc:
                _log(f"ensure_base64: load_image_from_path FAILED: {exc}", "WARNING", tag="媒体")
                result.append(img)
        else:
            is_b64 = len(img.data) > 100 and not img.data.startswith("data:")
            _log(f"ensure_base64: passthrough (is_url={img.is_url}, b64={is_b64}, len={len(img.data)})", "DEBUG", tag="媒体")
            result.append(img)
    return result


def compress_image_if_needed(
    image: ImageContent,
    max_kb: int = _MAX_IMAGE_KB,
) -> ImageContent:
    """如果图片超过指定大小则压缩（需要 Pillow）。"""
    if image.is_url:
        return image

    raw = base64.b64decode(image.data)
    if len(raw) <= max_kb * 1024:
        return image

    try:
        from PIL import Image as PILImage
    except ImportError:
        return image

    img = PILImage.open(io.BytesIO(raw))
    max_bytes = max_kb * 1024

    quality = 85
    while quality >= 10:
        buf = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
        else:
            img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= max_bytes:
            data = base64.b64encode(buf.getvalue()).decode("utf-8")
            return ImageContent(data=data, mime_type="image/jpeg")
        quality -= 15

    w, h = img.size
    ratio = (max_bytes / len(raw)) ** 0.5
    new_size = (int(w * ratio), int(h * ratio))
    img = img.resize(new_size, PILImage.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=60, optimize=True)
    data = base64.b64encode(buf.getvalue()).decode("utf-8")
    return ImageContent(data=data, mime_type="image/jpeg")


def qimage_to_image_content(qimage: object) -> Optional[ImageContent]:
    """将 Qt QImage 转为 ImageContent（剪贴板粘贴场景）。"""
    try:
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage

        if not isinstance(qimage, QImage) or qimage.isNull():
            return None

        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        qimage.save(buf, "PNG")
        raw = bytes(buf.data())
        buf.close()

        data = base64.b64encode(raw).decode("utf-8")
        return ImageContent(data=data, mime_type="image/png")
    except ImportError:
        return None


def is_image_file(path: str | Path) -> bool:
    """判断路径是否为支持的图片格式。"""
    return Path(path).suffix.lower() in _SUPPORTED_EXTENSIONS


def build_multimodal_content(
    text: str,
    images: List[ImageContent],
    *,
    flat_url: bool = False,
) -> list[dict]:
    """将文本和图片列表构建为多模态 content 数组。

    Args:
        flat_url: 为 True 时使用 Ollama 兼容的扁平 image_url 格式。
    """
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for img in images:
        parts.append(img.to_openai_block(flat_url=flat_url))
    return parts
