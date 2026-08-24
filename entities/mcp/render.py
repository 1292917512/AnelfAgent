"""MCP 调用结果渲染：CallToolResult → 工具结果文本（含图片落盘）。

内容块分派（对齐 dsh 的保真转换，图片更进一步直传）：text 拼接、
image 落盘 + ``_multimodal`` 约定、audio/resource 短占位、
structuredContent 兜底；base64 原文绝不进入上下文。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, List, Optional

from core.path import ConfigPaths

# 单次 MCP 工具结果允许注入的图片数上限（防上下文膨胀）
_MAX_RESULT_IMAGES = 4
# 单张注入图片的解码字节上限（防超大图灌满磁盘与多模态管道）
_MAX_IMAGE_BYTES = 8 * 1024 * 1024


async def _render_call_result(result: Any) -> str:
    """MCP CallToolResult → 工具结果文本。

    内容块分派（对齐 dsh 的保真转换，图片更进一步直传）：
    - text 块按序拼接；
    - image 块落盘为文件并经 ``_multimodal`` 约定返回——视觉模型在
      think_loop 侧直接"看到"图片（chrome-devtools/playwright 截图场景），
      非视觉模型读文本占位中的路径（可经 recognize_image 读取）；
      base64 原文绝不进入上下文（此前 str(item) 会倾倒整段 pydantic repr）；
    - audio/resource 块以短占位说明（防二进制灌入上下文）；
    - structuredContent 在无任何文本时兜底输出 JSON。

    模型可见性：无图片时输出纯文本（与旧版一致）；有图片时输出
    ``{"_multimodal": true, "text": ..., "images": [...]}`` JSON，由
    think_loop._append_multimodal_result 消费注入，注入位置在对话尾部
    user 消息，不影响缓存前缀。
    """
    import asyncio

    from core.config import get_config_bool

    passthrough = get_config_bool("mcp_image_passthrough", True)
    text_parts: List[str] = []
    placeholders: List[str] = []
    image_paths: List[str] = []
    dropped_images = 0

    for item in list(getattr(result, "content", None) or []):
        btype = getattr(item, "type", "")
        if btype == "text" or (not btype and hasattr(item, "text")):
            text_parts.append(str(getattr(item, "text", "") or ""))
        elif btype == "image":
            if not passthrough:
                dropped_images += 1
                continue
            data = str(getattr(item, "data", "") or "")
            mime = str(getattr(item, "mimeType", "") or "image/png")
            # 落盘放工作线程，避免大 base64 解码阻塞 bridge 事件循环
            path = await asyncio.to_thread(_save_mcp_image, data, mime)
            if path and len(image_paths) < _MAX_RESULT_IMAGES:
                image_paths.append(path)
            elif path:
                dropped_images += 1
            else:
                placeholders.append(f"[image: {mime}，数据解码失败或过大已丢弃]")
        elif btype == "audio":
            kb = len(str(getattr(item, "data", "") or "")) // 1024
            placeholders.append(
                f"[audio: {getattr(item, 'mimeType', '音频')}，约 {kb}KB 音频数据已丢弃]"
            )
        elif btype == "resource_link":
            placeholders.append(f"[resource: {getattr(item, 'uri', '')}]")
        elif btype == "resource":
            placeholders.append(_render_embedded_resource(item))
        else:
            placeholders.append(f"[未知内容块: {btype or type(item).__name__}]")

    if dropped_images:
        placeholders.append(
            f"[另有 {dropped_images} 张图片未注入"
            f"（超出单次上限 {_MAX_RESULT_IMAGES} 或已关闭 mcp_image_passthrough）]"
        )

    text = "\n".join(p for p in text_parts if p)
    if placeholders:
        text = (text + "\n" if text else "") + "\n".join(placeholders)

    structured = getattr(result, "structuredContent", None)
    if not text and isinstance(structured, dict) and structured:
        return json.dumps(structured, ensure_ascii=False)

    if image_paths:
        return json.dumps({
            "_multimodal": True,
            "text": text or "[系统] 上方工具返回了图片，请查看后继续。",
            "images": image_paths,
        }, ensure_ascii=False)
    return text


def _extract_text_blocks(result: Any) -> str:
    """提取结果中的全部 text 块（isError 路径用，不含占位与图片）。"""
    parts: List[str] = []
    for item in list(getattr(result, "content", None) or []):
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _render_embedded_resource(item: Any) -> str:
    """EmbeddedResource 块 → 文本提取或占位（blob 不进上下文）。"""
    res = getattr(item, "resource", None)
    uri = str(getattr(res, "uri", "") or "") if res is not None else ""
    if res is not None and getattr(res, "text", None):
        return f"[resource: {uri}]\n{res.text}"
    if res is not None and getattr(res, "blob", None):
        kb = len(str(res.blob)) // 1024
        return f"[resource: {uri}，二进制内容约 {kb}KB 已丢弃]"
    return f"[resource: {uri}]"


def _save_mcp_image(data_b64: str, mime_type: str) -> Optional[str]:
    """base64 图片落盘到 uploads/mcp/，返回路径；空数据/超大/解码失败返回 None。"""
    import base64
    import uuid as _uuid

    try:
        raw = base64.b64decode(data_b64 or "", validate=False)
    except Exception:
        return None
    if not raw or len(raw) > _MAX_IMAGE_BYTES:
        return None
    ext = "jpg" if "jpeg" in mime_type else mime_type.split("/")[-1] if "/" in mime_type else "png"
    ext = re.sub(r"[^A-Za-z0-9]", "", ext)[:8] or "png"
    folder = Path(ConfigPaths.UPLOAD_DIR) / "mcp"
    folder.mkdir(parents=True, exist_ok=True)
    fname = f"mcp_{int(time.time() * 1000)}_{_uuid.uuid4().hex[:6]}.{ext}"
    (folder / fname).write_bytes(raw)
    return str(folder / fname)
