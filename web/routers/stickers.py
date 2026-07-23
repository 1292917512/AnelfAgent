"""表情包与图片索引管理 API — WebUI 表情包页面的后端。

提供表情包的列表/上传/编辑/删除/重建索引，以及全量图片索引的浏览/删除；
图片文件经本路由按 ID/路径鉴权后回源（限定在 workspace 内）。

注意：/images/* 静态前缀路由必须先于 /{sticker_id} 参数化路由注册，
否则 "images" 会被当作 sticker_id 匹配。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.log import log

router = APIRouter(prefix="/stickers", tags=["stickers"])

_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _store():
    from entities.sticker.store import get_sticker_store
    return get_sticker_store()


def _workspace_abs() -> str:
    try:
        from core.config import ConfigManager
        ws = ConfigManager.get("workspace_root", "workspace")
    except Exception:
        ws = "workspace"
    return os.path.abspath(ws)


def _safe_file_response(path: str) -> FileResponse:
    """限定文件必须存在于 workspace 内，防止路径穿越。"""
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(_workspace_abs() + os.sep):
        raise HTTPException(status_code=403, detail="文件不在工作区内")
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(abs_path)


# ------------------------------------------------------------------
# 全量图片索引（静态前缀，先于 /{sticker_id} 注册）
# ------------------------------------------------------------------

@router.get("/images/list")
async def list_indexed_images(
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
) -> Dict[str, Any]:
    """分页浏览全量图片感知索引。"""
    return await _store().list_images(page=page, page_size=page_size)


@router.get("/images/file")
async def indexed_image_file(path: str) -> FileResponse:
    """索引图片预览（仅允许已入库且位于 workspace 内的路径）。"""
    record = await _store().get_image(path)
    if not record:
        raise HTTPException(status_code=404, detail="图片不在索引中")
    return _safe_file_response(path)


@router.delete("/images")
async def delete_indexed_image(path: str) -> Dict[str, Any]:
    """从索引中移除一张图片（不删除原文件）。"""
    if not await _store().delete_image(path):
        raise HTTPException(status_code=404, detail="图片不在索引中")
    return {"success": True, "removed": path}


# ------------------------------------------------------------------
# 表情包
# ------------------------------------------------------------------

@router.get("")
async def list_stickers(
    query: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
) -> Dict[str, Any]:
    """分页列出表情包（query 非空时在当前页内做模糊过滤）。"""
    return await _store().list_stickers(page=page, page_size=page_size, query=query)


@router.get("/stats")
async def sticker_stats() -> Dict[str, Any]:
    """表情包与图片索引统计。"""
    return await _store().stats()


@router.get("/{sticker_id}/file")
async def sticker_file(sticker_id: str) -> FileResponse:
    """表情包图片预览。"""
    sticker = await _store().get_sticker(sticker_id)
    if not sticker:
        raise HTTPException(status_code=404, detail="表情包不存在")
    return _safe_file_response(sticker["file_path"])


@router.post("")
async def upload_sticker(
    file: UploadFile = File(...),
    description: str = Form(""),
    tags: str = Form(""),
    emotion: str = Form(""),
) -> Dict[str, Any]:
    """上传表情包：description 留空时自动调用视觉模型生成。"""
    from entities.sticker.phash import compute_phash
    from entities.sticker.tools import (
        _describe_sticker, _embed_text, _import_to_stickers_dir,
        _md5_file, _parse_tags, _stickers_dir,
    )

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式: {ext or '未知'}")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="空文件")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件超过 20MB 限制")

    # 先落到 stickers 临时名，算出哈希后再规范命名
    tmp_path = os.path.join(_stickers_dir(), f"upload_{os.urandom(4).hex()}{ext}")
    with open(tmp_path, "wb") as f:
        f.write(data)

    try:
        content_hash = _md5_file(tmp_path)
        phash = compute_phash(tmp_path)
        tag_list = _parse_tags(tags)
        if not description.strip():
            description = await _describe_sticker(tmp_path)
        dest = _import_to_stickers_dir(tmp_path, content_hash)
        embedding = await _embed_text(description, tag_list)

        sticker = await _store().add_sticker(
            file_path=dest,
            description=description,
            tags=tag_list,
            emotion=emotion.strip(),
            content_hash=content_hash,
            phash=phash,
            source="webui",
            embedding=embedding,
        )
        log(f"WebUI 上传表情包: {sticker['id']}", tag="贴纸")
        return {"success": True, "sticker": sticker}
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


class StickerUpdate(BaseModel):
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    emotion: Optional[str] = None


@router.put("/{sticker_id}")
async def update_sticker(sticker_id: str, body: StickerUpdate) -> Dict[str, Any]:
    """更新描述/标签/情绪（自动重新生成检索向量）。"""
    from entities.sticker.tools import _embed_text

    store = _store()
    current = await store.get_sticker(sticker_id)
    if not current:
        raise HTTPException(status_code=404, detail="表情包不存在")

    new_desc = body.description if body.description is not None else current["description"]
    new_tags = body.tags if body.tags is not None else current["tags"]
    embedding = await _embed_text(new_desc, new_tags)

    updated = await store.update_sticker(
        sticker_id,
        description=body.description,
        tags=body.tags,
        emotion=body.emotion,
        embedding=embedding,
    )
    return {"success": True, "sticker": updated}


@router.post("/{sticker_id}/reindex")
async def reindex_sticker(sticker_id: str) -> Dict[str, Any]:
    """重新生成描述与检索向量（视觉模型重描述）。"""
    from entities.sticker.tools import _describe_sticker, _embed_text

    store = _store()
    current = await store.get_sticker(sticker_id)
    if not current:
        raise HTTPException(status_code=404, detail="表情包不存在")
    if not os.path.isfile(current["file_path"]):
        raise HTTPException(status_code=404, detail="表情包文件已丢失")

    description = await _describe_sticker(current["file_path"])
    if not description:
        raise HTTPException(status_code=503, detail="无可用视觉模型，无法重新生成描述")
    embedding = await _embed_text(description, current["tags"])
    updated = await store.update_sticker(
        sticker_id, description=description, embedding=embedding)
    return {"success": True, "sticker": updated}


@router.delete("/{sticker_id}")
async def delete_sticker(sticker_id: str) -> Dict[str, Any]:
    """删除表情包（连同文件与索引）。"""
    store = _store()
    removed = await store.delete_sticker(sticker_id)
    if not removed:
        raise HTTPException(status_code=404, detail="表情包不存在")
    try:
        if os.path.exists(removed["file_path"]):
            os.remove(removed["file_path"])
    except OSError as exc:
        log(f"表情包文件删除失败: {exc}", "DEBUG", tag="贴纸")
    return {"success": True, "removed": sticker_id}
