"""实时环境快照的文本与媒体消息构建。

模型可见：各提供者的最新快照与媒体，保留来源和层标记。
Token：沿用媒体上限与降级引用，不新增内容或调用。
缓存：仍位于工具链之后、执行状态之前，不修改历史前缀。
"""


from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional

from core.log import log

if TYPE_CHECKING:
    from agent.llm import ImageContent
    from agent.llm.config import LLMClientConfig


async def collect_provider_messages(config: "LLMClientConfig | None", scope: str) -> List[Dict]:
    """收集上下文提供者的当前最新快照，封装为注入消息列表。

    注入位置在工具链之后、exec_context 之前（每轮组装点调用）：
    时间/天气等实时内容逐轮新鲜，其字节变化又不打断工具链前缀缓存。
    收集失败 fail-open 为空列表（绝不影响主回复流程）。

    多模态契约（按 ContextMedia.kind 分派，物理约束见该类 docstring）：
    - clip 文本走 system 消息（与历史一致）；
    - image：视觉模型下集中组一条 user 角色多模态消息（image block 仅在
      user 角色可靠生效）；非视觉模型降级为 [media_type:image] 标签；
    - audio / video：一律降级为 [media_type:xxx][media_path:...] 标签
      并入所属 clip 的 system 文本（对话协议层不接受这两类 block；
      stable 层媒体规则已教会 AI 用对应工具处理标签）——不静默丢媒体。
    """
    try:
        from core.context_provider import ContextProviderRegistry
        clips, _metrics = await ContextProviderRegistry.collect(scope, fresh=True)
        vision = bool(config is not None and getattr(config, "supports_vision", False))

        messages: List[Dict] = []
        images: List["ImageContent"] = []
        for clip in clips:
            ref_lines: List[str] = []
            for m in clip.media:
                if m.kind == "image" and vision:
                    from agent.llm.types import ImageContent
                    images.append(ImageContent(
                        data=m.data, mime_type=m.mime_type or "image/jpeg",
                        is_url=m.is_url,
                    ))
                else:
                    # 降级路径：媒体以标签引用注入（与对话历史的媒体标签同构）
                    ref_lines.append(f"[media_type:{m.kind}][media_path:{m.data}]")
            text = clip.text
            if ref_lines:
                refs = " ".join(ref_lines)
                text = f"{text}\n{refs}" if text else f"[环境媒体] {refs}"
            if text:
                messages.append({
                    "role": "system",
                    "content": text,
                    "_layer": "provider",
                    "_source": {"origin": "context_provider", "provider": clip.source},
                })
        if images and config is not None:
            media_msg = await build_provider_media_message(images, config)
            if media_msg is not None:
                messages.append(media_msg)
            else:
                # 全部加载/下载失败：降级为标签引用，不静默丢图
                refs = " ".join(
                    f"[media_type:image][media_path:{i.data}]" for i in images
                )
                messages.append({
                    "role": "system",
                    "content": f"[环境媒体] {refs}（画面加载失败，未能直注）",
                    "_layer": "provider",
                    "_source": {"origin": "context_provider"},
                })
        return messages
    except Exception as exc:
        log(f"上下文提供者收集失败: {exc}", "DEBUG", tag="思维")
        return []


_MAX_PROVIDER_IMAGES = 3

def _sample_head_middle_tail(images: List["ImageContent"], limit: int) -> List["ImageContent"]:
    """等距抽样保头/中/尾（保持时间序=相关性序，不重复）。

    limit=3 时取首/中/尾三张——一段视觉材料信息量最高的是两端与中点。
    """
    if len(images) <= limit or limit <= 1:
        return images[:limit] if limit == 1 else images
    n = len(images)
    indexes = sorted({round(i * (n - 1) / (limit - 1)) for i in range(limit)})
    return [images[i] for i in indexes]


async def build_provider_media_message(
        images: List["ImageContent"],
        config: "LLMClientConfig",
) -> Optional[Dict]:
    """把 provider 快照附带的画面组一条 user 角色多模态注入消息（视觉模型）。

    视觉模型的 image block 只在 user 角色可靠生效（system 不行），故 provider
    的画面不走 system 注入块，而在此集中成一条紧随其后的 user 消息；
    图在文前（时间序=相关性序），位置在工具链之后、exec_context 之前，
    不触碰 stable/历史前缀缓存。无有效画面时返回 None（调用方不追加）。
    """
    from agent.llm.image_utils import ensure_base64_report
    from agent.llm.types import ImageContent

    sampled = _sample_head_middle_tail(images, _MAX_PROVIDER_IMAGES)
    prepared, url_fallbacks, failed = await ensure_base64_report(sampled)
    blocks: List[Dict] = [
        img.to_openai_block(flat_url=config.use_flat_image_url) for img in prepared
    ]
    for url in url_fallbacks:
        blocks.append(ImageContent(data=url, is_url=True).to_openai_block(
            flat_url=config.use_flat_image_url,
        ))
    if not blocks:
        return None
    notes: List[str] = []
    if len(images) > len(sampled):
        notes.append(f"共 {len(images)} 张，抽样展示 {len(sampled)} 张（头/中/尾）")
    if failed:
        notes.append(f"{len(failed)} 张加载失败未注入（{'; '.join(failed)}）")
    note_text = f"（{'；'.join(notes)}）" if notes else ""
    blocks.append({
        "type": "text",
        "text": f"[系统提示] 以上 {len(blocks)} 张图片是环境信息附带的实时画面"
                f"{note_text}，请结合上文环境信息理解。",
    })
    return {
        "role": "user",
        "content": blocks,
        "_layer": "provider",
        "_source": {"origin": "context_provider"},
    }
