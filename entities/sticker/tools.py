"""表情包与图片感知工具：收藏/检索/发送表情包，文搜图、图搜图、图片索引。

检索结果采用多模态约定：返回 {"_multimodal": true, "text": ..., "images": [...]} 时，
思维循环会把候选图片直接注入上下文，让视觉模型"亲眼看到"候选再决定使用哪张
（借鉴 nekro-agent 的 MULTIMODAL_AGENT 体验）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional

from core.config import register_configs_safe
from core.log import log
from entities._sdk import tool, entity

from .phash import compute_phash
from .store import StickerStore, get_sticker_store

entity(
    "sticker",
    "表情包与图片感知 - 收藏/语义检索/发送表情包，文搜图（全量图片语义检索）、图搜图（相似图片查找）",
)

_STICKER_CONFIGS = {
    "贴纸": {
        "image_index_enabled": {
            "description": "入站图片自动建立感知索引（phash + 描述 + 向量），支撑文搜图/图搜图",
            "default": True,
        },
        "image_index_describe_enabled": {
            "description": "索引时为图片调用视觉模型生成文字描述（关闭则仅保留 phash 索引，省 token）",
            "default": True,
        },
        "image_index_describe_timeout": {
            "description": "索引单张图片描述的超时时间（秒）",
            "default": 60.0,
        },
    },
}

register_configs_safe(_STICKER_CONFIGS)

# 收藏时的 VLM 描述提示：聚焦表情内容、情绪与适用场景（检索质量的关键）
_COLLECT_DESCRIBE_PROMPT = (
    "这是一张聊天表情包/梗图。请用一到两句话描述：画面主体是什么、表达的情绪或含义、"
    "图中的文字（如有）、适合在什么聊天情境下使用。只输出描述本身。"
)


def _stickers_dir() -> str:
    try:
        from core.config import ConfigManager
        ws = ConfigManager.get("workspace_root", "workspace")
    except Exception:
        ws = "workspace"
    path = os.path.join(os.path.abspath(ws), "stickers")
    os.makedirs(path, exist_ok=True)
    return path


def _resolve_path(path: str) -> str:
    """复用 media 实体的沙箱感知路径解析。"""
    from entities.media.tools import _resolve_workspace_path
    return _resolve_workspace_path(path)


async def _localize_source(source_path: str) -> tuple[str, str]:
    """将 URL / 本地路径落地为本地文件，返回 (local_path, error)。"""
    if not source_path:
        return "", "source_path 不能为空"
    if source_path.startswith(("http://", "https://")):
        from agent.channel.media import download_to_uploads
        from agent.channel.schemas import SegmentType
        local = await download_to_uploads(source_path, SegmentType.IMAGE)
        if not local:
            return "", f"图片下载失败: {source_path}"
        return local, ""
    try:
        resolved = _resolve_path(source_path)
    except ValueError as e:
        return "", str(e)
    if not os.path.exists(resolved):
        return "", f"文件不存在: {source_path}"
    return resolved, ""


def _md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _import_to_stickers_dir(local_path: str, content_hash: str) -> str:
    """把图片复制进 stickers 目录，文件名带哈希后缀防冲突。"""
    ext = os.path.splitext(local_path)[1].lower() or ".jpg"
    dest = os.path.join(
        _stickers_dir(),
        f"sticker_{int(time.time())}_{content_hash[:6]}{ext}",
    )
    shutil.copy2(local_path, dest)
    return dest


async def _describe_sticker(local_path: str) -> str:
    """VLM 生成表情包描述（遍历视觉客户端直到成功，失败返回空串）。"""
    try:
        from entities._sdk import (
            get_llm_manager, get_model_type_enum, load_image_from_path,
        )
        mgr = get_llm_manager()
        ModelType = get_model_type_enum()
        for vc in mgr.get_all_by_type(ModelType.VISION):
            try:
                img = load_image_from_path(local_path)
                return await vc.describe_images([img], prompt=_COLLECT_DESCRIBE_PROMPT)
            except Exception as exc:
                log(f"表情包描述模型 {vc.config.name} 失败: {exc}", "DEBUG", tag="贴纸")
                continue
    except Exception as exc:
        log(f"视觉模型不可用: {exc}", "DEBUG", tag="贴纸")
    return ""


_embedder: Any = None


def _get_embedder() -> Any:
    global _embedder
    if _embedder is None:
        from agent.memory.embedder import Embedder
        _embedder = Embedder()
    return _embedder


async def _embed_text(description: str, tags: List[str]) -> Optional[list]:
    """embedding 文本 = description + tags（与 nekro 一致的索引模型）。"""
    text = f"{description} {' '.join(tags)}".strip()
    if not text:
        return None
    try:
        return await _get_embedder().embed_one(text)
    except Exception as exc:
        log(f"embedding 失败（降级关键词检索）: {exc}", "DEBUG", tag="贴纸")
        return None


def _parse_tags(tags: str) -> List[str]:
    """解析逗号/空格分隔的标签字符串为列表。"""
    if not tags:
        return []
    parts = tags.replace("，", ",").replace("、", ",").split(",")
    result: List[str] = []
    for part in parts:
        result.extend(t for t in part.split() if t)
    return [t.strip() for t in result if t.strip()][:10]


def _candidate_brief(idx: int, item: Dict[str, Any]) -> str:
    score = item.get("score")
    score_text = f", 相关度 {score}" if score is not None else ""
    tags = "、".join(item.get("tags") or [])
    tags_text = f", 标签: {tags}" if tags else ""
    return (
        f"{idx}. ID={item['id']}{score_text}{tags_text}\n"
        f"   描述: {item.get('description', '')[:120]}"
    )


def _multimodal_result(items: List[Dict[str, Any]], header: str, kind: str) -> str:
    """构建多模态检索结果：思维循环会把 images 注入上下文让模型直接看到候选。"""
    existing = [i for i in items if os.path.exists(i.get("file_path", ""))]
    if not existing:
        return json.dumps({"results": [], "hint": "未找到匹配结果"}, ensure_ascii=False)
    lines = [header]
    for idx, item in enumerate(existing, 1):
        lines.append(_candidate_brief(idx, item))
    lines.append(
        f"候选图片已附上，请查看后选择。使用方式：send_{kind}(id=...)"
        if kind == "sticker" else "候选图片已附上，可直接查看。"
    )
    return json.dumps({
        "_multimodal": True,
        "text": "\n".join(lines),
        "images": [i["file_path"] for i in existing],
        "results": [
            {"id": i["id"], "description": i.get("description", "")[:100],
             "tags": i.get("tags") or [], "score": i.get("score")}
            for i in existing
        ],
    }, ensure_ascii=False)


# ==================================================================
# 表情包：收藏
# ==================================================================

@tool(name="collect_sticker", group="sticker", tags=["media:image"], timeout=120.0)
async def collect_sticker(
    source_path: str,
    description: str = "",
    tags: str = "",
    emotion: str = "",
) -> str:
    """收藏聊天中出现的表情包/梗图到你的表情库，之后可用 search_sticker 检索、send_sticker 发送。

    收藏纪律（必须遵守）：
    - 只收藏真正的表情包/梗图/反应图；不要收藏普通照片、截图、文档图片
    - 只能收藏对话中实际出现过的图片（用户发送或你生成/发送过的），不要凭空收藏
    - description 留空时自动调用视觉模型生成描述，通常无需手动填写

    Args:
        source_path: 图片路径或 URL（来自消息中的 [media_file:image:路径] 标签）
        description: 可选，手动指定描述（画面内容+情绪+适用场景）；留空自动生成
        tags: 可选，逗号分隔的检索标签，如 "开心,猫,点赞"
        emotion: 可选，情绪标签，如 "开心" "无语" "阴阳怪气"
    """
    local_path, err = await _localize_source(source_path)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)

    try:
        content_hash = await asyncio.to_thread(_md5_file, local_path)
        phash = await asyncio.to_thread(compute_phash, local_path)
        tag_list = _parse_tags(tags)

        if not description.strip():
            description = await _describe_sticker(local_path)
            if not description:
                return json.dumps({
                    "error": "未配置可用的视觉模型，无法自动生成描述；"
                             "请提供 description 参数手动描述后重试",
                }, ensure_ascii=False)

        dest = _import_to_stickers_dir(local_path, content_hash)
        embedding = await _embed_text(description, tag_list)

        store = get_sticker_store()
        sticker = await store.add_sticker(
            file_path=dest,
            description=description,
            tags=tag_list,
            emotion=emotion.strip(),
            content_hash=content_hash,
            phash=phash,
            source=source_path[:200],
            embedding=embedding,
        )
        log(f"表情包已收藏: {sticker['id']} ({description[:40]})", tag="贴纸")
        return json.dumps({
            "success": True,
            "sticker_id": sticker["id"],
            "description": description,
            "tags": tag_list,
            "deduplicated": sticker.get("deduplicated", False),
            "hint": "已通过 search_sticker 可检索；发送用 send_sticker",
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": f"收藏失败: {exc}"}, ensure_ascii=False)


# ==================================================================
# 表情包：检索 / 发送 / 管理
# ==================================================================

@tool(name="search_sticker", group="sticker", tags=["always"], concurrency_safe=True)
async def search_sticker(query: str, limit: int = 3) -> str:
    """按语义检索已收藏的表情包，候选图片会直接展示给你查看。

    想用表情包活跃气氛、回应情绪时使用。检索后你可以直接看到候选图片，
    挑选最合适的一张用 send_sticker 发送；若候选与意图不符，可用
    update_sticker 修正描述、remove_sticker 清理，保持收藏质量。

    Args:
        query: 语义描述，如 "开心的猫" "阴阳怪气" "无语望天"
        limit: 返回候选数量，默认 3
    """
    store = get_sticker_store()
    try:
        query_vec = await _get_embedder().embed_one(query)
    except Exception:
        query_vec = None
    items = await store.search_stickers(query, query_vec=query_vec, limit=max(1, min(limit, 10)))
    if not items:
        return json.dumps({
            "results": [],
            "hint": "表情库为空或无匹配；看到喜欢的表情包时用 collect_sticker 收藏",
        }, ensure_ascii=False)
    return _multimodal_result(items, f"找到 {len(items)} 个候选表情包：", "sticker")


@tool(name="send_sticker", group="sticker", tags=["always"], timeout=60.0)
async def send_sticker(
    sticker_id: str,
    channel_id: str,
    target_id: str,
    caption: str = "",
) -> str:
    """把表情库中的表情包发送到指定频道会话。

    Args:
        sticker_id: 表情包 ID（通过 search_sticker / list_stickers 获取）
        channel_id: 频道标识（通过 list_channels 获取）
        target_id: 目标会话 ID（用户 uid 或群组 group_id，来自消息标签）
        caption: 可选附言
    """
    store = get_sticker_store()
    sticker = await store.get_sticker(sticker_id)
    if not sticker:
        return json.dumps({
            "error": f"表情包不存在: {sticker_id}",
            "hint": "请先用 search_sticker 或 list_stickers 确认可用 ID",
        }, ensure_ascii=False)
    file_path = sticker["file_path"]
    if not os.path.exists(file_path):
        return json.dumps({"error": f"表情包文件已丢失: {file_path}"}, ensure_ascii=False)

    from agent.channel.output_tools import _execute_send_action

    async def _invoke(ch: Any, resolved_target_id: str, channel_type: str) -> Any:
        return await ch.send_photo(
            resolved_target_id, file_path, caption=caption, channel_type=channel_type)

    def _enrich(parsed: dict, ok: bool) -> None:
        parsed["sticker_id"] = sticker_id
        if ok:
            parsed["sent_media"] = f"[media_type:image][media_path:{file_path}]"

    result = await _execute_send_action(
        channel_id=channel_id,
        target_id=target_id,
        operation="表情包",
        invoke=_invoke,
        enrich=_enrich,
    )
    try:
        if json.loads(result).get("success") is not False:
            await store.touch_use(sticker_id)
    except (json.JSONDecodeError, TypeError):
        pass
    return result


@tool(name="list_stickers", group="sticker", tags=["always"], concurrency_safe=True)
async def list_stickers(page: int = 1, page_size: int = 20) -> str:
    """列出已收藏的表情包（按最近更新排序）。

    Args:
        page: 页码，从 1 开始
        page_size: 每页数量，默认 20
    """
    store = get_sticker_store()
    data = await store.list_stickers(page=page, page_size=max(1, min(page_size, 50)))
    items = [
        {
            "id": s["id"],
            "description": s["description"][:80],
            "tags": s["tags"],
            "emotion": s["emotion"],
            "use_count": s["use_count"],
        }
        for s in data["items"]
    ]
    return json.dumps({
        "items": items, "total": data["total"],
        "page": data["page"], "page_size": data["page_size"],
    }, ensure_ascii=False)


@tool(name="update_sticker", group="sticker", tags=["sticker"])
async def update_sticker(
    sticker_id: str,
    description: str = "",
    tags: str = "",
    emotion: str = "",
) -> str:
    """更新表情包的描述/标签/情绪（会自动重新生成检索向量）。

    当 search_sticker 的候选与描述不符时用于修正，保持收藏质量。

    Args:
        sticker_id: 表情包 ID
        description: 新描述（留空保持不变）
        tags: 新标签，逗号分隔（留空保持不变）
        emotion: 新情绪标签（留空保持不变）
    """
    store = get_sticker_store()
    current = await store.get_sticker(sticker_id)
    if not current:
        return json.dumps({"error": f"表情包不存在: {sticker_id}"}, ensure_ascii=False)

    new_desc = description.strip() or current["description"]
    new_tags = _parse_tags(tags) if tags.strip() else current["tags"]
    embedding = await _embed_text(new_desc, new_tags)

    updated = await store.update_sticker(
        sticker_id,
        description=new_desc if description.strip() else None,
        tags=new_tags if tags.strip() else None,
        emotion=emotion.strip() or None,
        embedding=embedding,
    )
    return json.dumps({"success": True, "sticker": updated}, ensure_ascii=False)


@tool(name="remove_sticker", group="sticker", tags=["sticker"])
async def remove_sticker(sticker_id: str) -> str:
    """从表情库删除一个表情包（连同文件和索引一起移除）。

    用于清理不是表情包的误收藏、重复或低质量条目。

    Args:
        sticker_id: 表情包 ID
    """
    store = get_sticker_store()
    removed = await store.delete_sticker(sticker_id)
    if not removed:
        return json.dumps({"error": f"表情包不存在: {sticker_id}"}, ensure_ascii=False)
    try:
        if os.path.exists(removed["file_path"]):
            os.remove(removed["file_path"])
    except OSError as exc:
        log(f"表情包文件删除失败: {exc}", "DEBUG", tag="贴纸")
    return json.dumps({"success": True, "removed": sticker_id}, ensure_ascii=False)


# ==================================================================
# 全量图片感知：文搜图 / 图搜图 / 手动索引
# ==================================================================

@tool(name="search_image", group="sticker", tags=["always"], concurrency_safe=True)
async def search_image(query: str, limit: int = 5) -> str:
    """文搜图：按文字描述语义检索所有出现过的图片（聊天收发的图片会自动建立感知索引），候选图直接展示给你查看。

    适用于"找一下之前那张架构图""那张猫的照片还在吗"这类请求。

    Args:
        query: 对图片内容的描述，如 "系统架构图" "戴帽子的猫"
        limit: 返回候选数量，默认 5
    """
    store = get_sticker_store()
    try:
        query_vec = await _get_embedder().embed_one(query)
    except Exception:
        query_vec = None
    items = await store.search_images(query, query_vec=query_vec, limit=max(1, min(limit, 10)))
    if not items:
        return json.dumps({
            "results": [],
            "hint": "图片索引为空或无匹配（入站图片会自动索引，也可用 index_image 手动索引）",
        }, ensure_ascii=False)
    for item in items:
        item["file_path"] = item.get("path", "")
        item["id"] = item.get("path", "")
        item.setdefault("tags", [])
    return _multimodal_result(items, f"找到 {len(items)} 张匹配图片：", "image")


@tool(name="find_similar_image", group="sticker", tags=["media:image"], concurrency_safe=True)
async def find_similar_image(image_path: str, limit: int = 5) -> str:
    """图搜图：查找与给定图片相同或相似的图片（感知哈希比对，对缩放/压缩/轻微编辑鲁棒），候选图直接展示给你查看。

    适用于"这张图之前是不是发过""有没有类似的表情包"。

    Args:
        image_path: 参考图片路径或 URL（可用消息中的 [media_file:image:路径]）
        limit: 返回候选数量，默认 5
    """
    local_path, err = await _localize_source(image_path)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)

    phash = await asyncio.to_thread(compute_phash, local_path)
    if not phash:
        return json.dumps({"error": f"无法解析图片: {image_path}"}, ensure_ascii=False)

    store = get_sticker_store()
    items = await store.find_similar_by_phash(phash, limit=max(1, min(limit, 10)))
    # 排除自身（同内容哈希）
    try:
        self_hash = await asyncio.to_thread(_md5_file, local_path)
        items = [i for i in items if i.get("content_hash") != self_hash]
    except OSError:
        pass
    if not items:
        return json.dumps({"results": [], "hint": "未找到相同或相似的图片"}, ensure_ascii=False)
    for item in items:
        item["id"] = item.get("id") or item.get("path", "")
        item.setdefault("tags", [])
        item["description"] = (
            f"[{'表情包' if item.get('kind') == 'sticker' else '图片'}, "
            f"相似差异 {item['distance']}/64] " + item.get("description", "")
        )
    return _multimodal_result(items, f"找到 {len(items)} 张相同/相似图片：", "image")


@tool(name="index_image", group="sticker", tags=["sticker"], timeout=120.0)
async def index_image(image_path: str, description: str = "") -> str:
    """手动把一张图片加入感知索引（通常无需调用——聊天中出现的图片会自动索引）。

    Args:
        image_path: 图片路径或 URL
        description: 可选描述；留空时自动调用视觉模型生成
    """
    local_path, err = await _localize_source(image_path)
    if err:
        return json.dumps({"error": err}, ensure_ascii=False)

    try:
        content_hash = await asyncio.to_thread(_md5_file, local_path)
        store = get_sticker_store()
        if await store.get_image_by_hash(content_hash):
            return json.dumps({"success": True, "hint": "该图片已在索引中（内容去重）"},
                              ensure_ascii=False)
        phash = await asyncio.to_thread(compute_phash, local_path)
        if not description.strip():
            description = await _describe_sticker(local_path)
        embedding = await _embed_text(description, [])
        await store.upsert_image(
            path=local_path,
            description=description,
            content_hash=content_hash,
            phash=phash,
            source="manual",
            embedding=embedding,
        )
        return json.dumps({
            "success": True, "path": local_path,
            "description": description, "phash": phash,
        }, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": f"索引失败: {exc}"}, ensure_ascii=False)
