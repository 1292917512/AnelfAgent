"""图片内容块的准备与当前上下文内去重。

模型可见：原图及加载失败提示经 user 多模态消息呈现，不生成图片描述。
Token：相同图像内容在当前上下文中只保留一份，避免重复视觉输入。
缓存：只生成待追加的内容块，不修改已有消息或稳定前缀。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.llm import image_utils
from agent.llm.config import LLMClientConfig
from agent.llm.types import ImageContent


def _image_key(url: str) -> str:
    """内联图按编码内容判重，远程图按完整地址判重。"""
    return url.partition(",")[2] if url.startswith("data:image/") else url


async def build_image_blocks(
    images: list[ImageContent],
    config: LLMClientConfig,
    existing_messages: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """按传输能力准备原图，省略当前上下文已有的图片并明确报告加载失败。"""
    sources = list({
        (image.data, image.mime_type): ImageContent(
            image.data, image.mime_type, image.data.startswith(("http://", "https://")),
        )
        for image in images
    }.values())
    duplicates = 0
    if config.supports_base64_vision:
        prepared, fallbacks, failed = await image_utils.ensure_base64_report(sources)
        if not config.supports_url_vision:
            failed.extend(image.data[:80] for image in prepared if image.is_url)
            prepared = [image for image in prepared if not image.is_url]
            fallbacks = []
    else:
        prepared = [image for image in sources if image.is_url]
        failed = [image.data[:80] for image in sources if not image.is_url]
        fallbacks = []
    seen: set[str] = set()
    for message in existing_messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "image_url":
                continue
            value = block.get("image_url")
            url = value.get("url") if isinstance(value, dict) else value
            if isinstance(url, str):
                seen.add(_image_key(url))
    blocks: list[dict[str, Any]] = []
    for image in prepared:
        block = image.to_openai_block(flat_url=config.use_flat_image_url)
        key = _image_key(image.data)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        blocks.append(block)
    notices: list[str] = []
    if duplicates:
        notices.append(f"{duplicates} 张相同图片已在当前上下文中，请直接查看已有原图，无需再次识别。")
    if fallbacks:
        notices.append(
            f"{len(fallbacks)} 张图片本地下载失败（{'; '.join(fallbacks)}），已按原 URL 直传给端点；"
            "若仍看不到图片，请如实说明，不要描述未看到的内容。"
        )
    if failed:
        notices.append(
            f"{len(failed)} 张图片加载失败或不符合当前模型的图片传输格式"
            f"（{'; '.join(failed)}），未包含在消息中，请如实说明。"
        )
    if notices:
        blocks.append({"type": "text", "text": "[系统提示] " + " ".join(notices)})
    return blocks
