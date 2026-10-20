"""会话媒体处理：视觉模型直传、非视觉模型引用与图片转存。

模型可见：入站图片和工具图片共用原图加载，失败明确提示，不另起识图调用。
Token：工具内联图片与文字分离；当前上下文中的相同原图不重复注入。
缓存：入站图片保留消息归属，工具图片追加在整批回执之后，不改已有前缀。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Dict, List, Optional

from agent.context.media import build_image_blocks
from agent.llm.types import ImageContent
from core.log import log
from core.tool_results import parse_tool_result_json

if TYPE_CHECKING:
    from agent.llm.llm_client import LLMClientConfig
    from agent.messages import Everything
    from agent.mind.mind import Mind


# 内联 base64 图片数据的长度上限：超过则转存为文件路径（防上下文膨胀）
_MAX_INLINE_IMAGE_DATA = 500


def save_base64_image(b64_data: str, mime_type: str = "image/jpeg") -> str:
    """将 base64 图片数据保存为文件，返回路径。"""
    import base64
    import time as _time

    from core.path import ConfigPaths

    ext = "jpg" if "jpeg" in mime_type else mime_type.split("/")[-1] if "/" in mime_type else "jpg"
    upload_dir = os.path.join(str(ConfigPaths.UPLOAD_DIR), "image")
    os.makedirs(upload_dir, exist_ok=True)
    fname = f"vision_{int(_time.time() * 1000)}_{uuid.uuid4().hex[:6]}.{ext}"
    fpath = os.path.join(upload_dir, fname)
    with open(fpath, "wb") as f:
        f.write(base64.b64decode(b64_data))
    return fpath


async def apply_vision(
        mind: Mind,
        messages: List[Dict],
        images: List[ImageContent],
        anything: Optional[Everything] = None,
        *,
        existing_messages: Sequence[Mapping] = (),
) -> List[Dict]:
    """处理图片：视觉模型直接注入图片 block，其余大 base64 图转存为文件路径。

    视觉模型（supports_vision）：图片以多模态 content block 附着到最后一条
    user 消息，LLM 直接"看到"图片，无需 recognize_image 工具中转；
    消息含图片 block 时 chat_with_fallback 的回退链自动收敛到视觉候选。

    非视觉模型：图片标签已由 add_conversation_record_by_everything 写入用户
    消息（持久化），此处不再重复写 system 消息或追加标签（避免 user/system/
    内存三处重复）。仅当图片是超大 base64 数据时转存为文件，并更新用户消息
    中的标签路径。
    """
    if not images:
        return messages

    log(f"processing {len(images)} image(s)", tag="思维")

    config = getattr(mind.models.llm, "config", None)
    if config is not None and getattr(config, "supports_vision", False):
        return await _inject_image_blocks(messages, images, config, existing_messages=existing_messages)

    # 仅处理需要转存的超大 base64 图片（QQ/Telegram 通常是 URL/文件路径，无需处理）
    saver = save_base64_image

    path_map: Dict[str, str] = {}
    for img in images:
        path = img.data
        if not img.is_url and len(path) > _MAX_INLINE_IMAGE_DATA:
            # 写盘放工作线程，避免大 base64 解码阻塞事件循环
            path_map[path] = await asyncio.to_thread(saver, path, img.mime_type)

    if not path_map:
        return messages

    # 将用户消息中的 base64 标签路径替换为转存后的文件路径
    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") == "user":
            c = result[i].get("content", "")
            if isinstance(c, str):
                for old_path, new_path in path_map.items():
                    c = c.replace(f"[media_path:{old_path}]", f"[media_path:{new_path}]")
                result[i] = {**result[i], "content": c}
            break
    return result


async def _inject_image_blocks(
        messages: List[Dict],
        images: List[ImageContent],
        config: "LLMClientConfig",
        *,
        existing_messages: Sequence[Mapping] = (),
) -> List[Dict]:
    """将新图片附到最后一条 user 消息，复用当前上下文中的原图。"""
    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") != "user":
            continue
        blocks = await build_image_blocks(images, config, [*existing_messages, *messages])
        content = result[i].get("content", "")
        if isinstance(content, str):
            parts: List[Dict] = [{"type": "text", "text": content}] if content else []
        elif isinstance(content, list):
            parts = list(content)
        else:
            parts = []
        result[i] = {**result[i], "content": parts + blocks}
        count = sum(block["type"] == "image_url" for block in blocks)
        log(f"图片直传: {count} 张注入到最后一条 user 消息", "DEBUG", tag="思维")
        break
    return result


# 单个工具结果允许附带的最大图片数（防上下文膨胀）
_MAX_TOOL_RESULT_IMAGES = 6


def split_multimodal_result(output: str) -> tuple[str, list[ImageContent]]:
    """在文本预算处理前提取工具原图，内联数据不作为普通文字重复投送。"""
    if '"_multimodal"' not in output:
        return output, []
    parsed = parse_tool_result_json(output)
    if not isinstance(parsed, dict) or not parsed.get("_multimodal") or parsed.get("success") is False:
        return output, []
    references = parsed.get("images")
    if not isinstance(references, list):
        return output, []
    images = [ImageContent(data=p, is_url=p.startswith(("http://", "https://")))
              for p in references if isinstance(p, str) and p][:_MAX_TOOL_RESULT_IMAGES]
    parsed["images"] = ["[内联图片]" if image.data.startswith("data:") else image.data for image in images]
    if isinstance(parsed.get("image_path"), str) and parsed["image_path"].startswith("data:"):
        parsed["image_path"] = "[内联图片]"
    return json.dumps(parsed, ensure_ascii=False), images


async def _append_multimodal_result(
        mind: Mind,
        tool_chain: List[Dict],
        images: list[ImageContent],
        *,
        existing_messages: Sequence[Mapping] = (),
) -> None:
    """将工具原图或加载失败提示追加到批次尾部，已有原图只追加复用提示。"""
    if not images:
        return
    config = getattr(mind.models.llm, "config", None)
    if config is None or not getattr(config, "supports_vision", False):
        return

    blocks = await build_image_blocks(images, config, [*existing_messages, *tool_chain])
    if blocks:
        tool_chain.append({
            "role": "user", "content": [{"type": "text", "text": "上方工具返回的图片，请结合调用要求查看。"}, *blocks],
            "_layer": "tool_chain", "_source": {"origin": "tool_media"},
        })
    count = sum(block["type"] == "image_url" for block in blocks)
    log(f"多模态工具结果: 注入 {count} 张候选图片", "DEBUG", tag="思维")
