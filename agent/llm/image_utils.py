"""图片处理工具：加载、压缩、格式转换。"""

from __future__ import annotations

import base64
import io
import mimetypes
from pathlib import Path
from typing import List, Optional

from agent.llm.types import ImageContent

_MAX_LONG_EDGE = 1568
_MAX_IMAGE_KB = 1024
_SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


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


# 常见图片格式的 base64 魔数前缀（避免把短 base64 误判为文件路径）
_BASE64_MAGIC_PREFIXES = ("/9j/", "iVBOR", "R0lGOD", "UklGR", "Qk2", "SUkq")


def _looks_like_file_path(data: str) -> bool:
    """Detect if data string is a local file path rather than base64."""
    if data.startswith(_BASE64_MAGIC_PREFIXES):
        return False
    return (len(data) < 500
            and ("/" in data or "\\" in data)
            and not data.startswith("data:"))


async def ensure_base64(images: List[ImageContent]) -> List[ImageContent]:
    """确保所有图片都是 base64 格式，并自动压缩优化。

    自动处理三种来源：
    - URL → 下载并编码（失败保留原 URL，交由端点拉取）
    - 本地文件路径 → 读取并编码（失败则丢弃该图片，避免脏数据注入 LLM）
    - 已是 base64 → 直接使用

    每张图片加载后自动经过 optimize_for_vision 压缩。
    """
    from core.log import log as _log

    result: List[ImageContent] = []
    for img in images:
        if img.is_url:
            _log(f"ensure_base64: downloading URL ({img.data[:80]})", "DEBUG", tag="媒体")
            converted = await download_image_to_base64(img.data)
            loaded = converted if converted else img
        elif _looks_like_file_path(img.data):
            _log(f"ensure_base64: loading local file ({img.data})", "DEBUG", tag="媒体")
            try:
                loaded = load_image_from_path(img.data)
            except Exception as exc:
                _log(f"ensure_base64: 图片加载失败，已丢弃 ({img.data}): {exc}", "WARNING", tag="媒体")
                continue
        else:
            loaded = img
        result.append(optimize_for_vision(loaded))
    return result


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
        img = PILImage.open(io.BytesIO(raw))
    except Exception:
        return image

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

    from core.log import log as _log
    _log(
        f"图片优化: {src_format} {w}x{h} ({original_bytes // 1024}KB) → "
        f"JPEG {img.size[0]}x{img.size[1]} ({len(compressed) // 1024}KB)",
        "DEBUG", tag="媒体",
    )
    return ImageContent(data=base64.b64encode(compressed).decode("utf-8"), mime_type="image/jpeg")


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
