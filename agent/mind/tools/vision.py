"""思维循环的图片处理块：视觉模型直传与 base64 转存。

从 think_loop 拆出，函数以 mind 实例为第一参数；
think_loop 通过 import 复用本模块（并保持同名再导出以兼容既有引用）。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import TYPE_CHECKING, Dict, List, Optional

from core.log import log

if TYPE_CHECKING:
    from agent.llm import ImageContent
    from agent.llm.llm_client import LLMClientConfig
    from agent.messages import Everything
    from agent.mind.mind import Mind


def collect_pending_images(mind: Mind, scope: str = "") -> List[ImageContent]:
    return mind.pfc.collect_images(scope=scope)


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

    config = getattr(getattr(mind, "llm", None), "config", None)
    if config is not None and getattr(config, "supports_vision", False):
        return await _inject_image_blocks(messages, images, config)

    # 仅处理需要转存的超大 base64 图片（QQ/Telegram 通常是 URL/文件路径，无需处理）
    # 经 think_loop 命名空间解析转存函数：兼容既有打桩路径
    # agent.mind.tools.think_loop.save_base64_image（think_loop 再导出本模块实现）
    from agent.mind.tools import think_loop as _think_loop_mod
    saver = getattr(_think_loop_mod, "save_base64_image", save_base64_image)

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
) -> List[Dict]:
    """将图片以多模态 content block 注入到最后一条 user 消息（视觉模型直传）。

    按模型 vision_format 逐张协商图片形式：
    - URL 且模型支持 url 视觉：原样引用，不下载
    - 其余（本地路径 / base64 / 模型仅支持 base64）：统一归一为压缩后的 base64

    注入位置在对话尾部，stable/volatile 前缀字节不变，Prompt Caching 不受影响。
    """
    from agent.llm.image_utils import ensure_base64

    prepared: List[ImageContent] = []
    failed: List[str] = []
    for img in images:
        if img.is_url and config.supports_url_vision:
            prepared.append(img)
        else:
            converted = await ensure_base64([img])
            if converted:
                prepared.extend(converted)
            else:
                failed.append(img.data[:80])
    blocks = [img.to_openai_block(flat_url=config.use_flat_image_url) for img in prepared]
    if failed:
        blocks.append({
            "type": "text",
            "text": f"[系统提示] {len(failed)} 张图片加载失败（{'; '.join(failed)}），未包含在消息中，请告知用户。",
        })

    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") != "user":
            continue
        content = result[i].get("content", "")
        if isinstance(content, str):
            parts: List[Dict] = [{"type": "text", "text": content}] if content else []
        elif isinstance(content, list):
            parts = list(content)
        else:
            parts = []
        result[i] = {**result[i], "content": parts + blocks}
        log(f"图片直传: {len(prepared)} 张注入到最后一条 user 消息", "DEBUG", tag="思维")
        break
    return result


# 单个工具结果允许附带的最大图片数（防上下文膨胀）
_MAX_TOOL_RESULT_IMAGES = 6


async def _append_multimodal_result(
        mind: Mind,
        tool_chain: List[Dict],
        output: str,
) -> None:
    """展开多模态工具结果约定，把候选图片以 user 消息注入上下文。

    工具返回 JSON 含 ``{"_multimodal": true, "text": ..., "images": [路径...]}``
    时（如 search_sticker / search_image / find_similar_image），将图片加载
    压缩后以 image_url block 注入，视觉模型即可"亲眼看到"候选再做选择
    （借鉴 nekro-agent MULTIMODAL_AGENT 的检索体验）。非视觉模型跳过，
    文本摘要（text/results 字段）已随 tool 消息提供全部信息。
    """
    if '"_multimodal"' not in output:
        return
    # 延迟导入：round_helpers 依赖本模块的 apply_vision，避免循环引用
    from agent.mind.tools.round_helpers import _parse_tool_result_json
    parsed = _parse_tool_result_json(output)
    if not isinstance(parsed, dict) or not parsed.get("_multimodal"):
        return
    images = [p for p in (parsed.get("images") or []) if isinstance(p, str) and p]
    if not images:
        return
    config = getattr(getattr(mind, "llm", None), "config", None)
    if config is None or not getattr(config, "supports_vision", False):
        return

    from agent.llm.image_utils import ensure_base64, load_image_from_path

    candidates: List[ImageContent] = []
    for path in images[:_MAX_TOOL_RESULT_IMAGES]:
        try:
            candidates.append(load_image_from_path(path))
        except Exception:
            continue
    if not candidates:
        return
    prepared = await ensure_base64(candidates)
    if not prepared:
        return
    text = parsed.get("text") or "[系统] 上方工具返回了候选图片，请查看后继续。"
    blocks: List[Dict] = [{"type": "text", "text": text}]
    blocks.extend(img.to_openai_block(flat_url=config.use_flat_image_url) for img in prepared)
    tool_chain.append({"role": "user", "content": blocks})
    log(f"多模态工具结果: 注入 {len(prepared)} 张候选图片", "DEBUG", tag="思维")
