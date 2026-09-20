"""工作区文件 API 路由 — 浏览、读取、编辑、搜索 workspace/ 目录。

业务逻辑全部在 services.workspace.WorkspaceService（沙箱规则 / 配额 / 搜索），
本模块只做参数校验与 WorkspaceError → HTTP 响应映射。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from services.workspace import WorkspaceError, WorkspaceService

router = APIRouter(prefix="/workspace", tags=["workspace"])

_svc = WorkspaceService()


@router.get("/tree")
async def get_tree(
    path: str = Query(""),
    depth: int = Query(2, ge=1, le=6),
    root: str = Query("workspace"),
) -> Dict[str, Any]:
    """获取目录树（默认两层，懒加载可传子路径；root=project 时浏览项目根）。"""
    # 目录遍历为同步磁盘 I/O（项目根 3000 条配额），移入线程避免阻塞事件循环
    try:
        return await asyncio.to_thread(_svc.get_tree, path, depth, root)
    except WorkspaceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@router.get("/file")
async def read_file(path: str = Query(...), root: str = Query("workspace")) -> Dict[str, Any]:
    """读取文本文件内容（上限 512KB，二进制文件只返回元信息）。"""
    try:
        return await asyncio.to_thread(_svc.read_file, path, root)
    except WorkspaceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@router.get("/raw")
async def serve_raw_file(path: str = Query(...), inline: bool = False, root: str = Query("workspace")) -> Any:
    """以原始字节服务文件（图片/音视频/PDF 预览用），按扩展名推断 Content-Type。

    inline=True 时以 inline 方式返回（供 iframe 内联渲染），默认 attachment（下载语义）。
    """
    from starlette.responses import FileResponse
    try:
        fp = _svc.resolve_existing_file(path, root)
    except WorkspaceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return FileResponse(
        fp,
        filename=os.path.basename(fp),
        content_disposition_type="inline" if inline else "attachment",
    )


class FileWriteRequest(BaseModel):
    path: str
    content: str
    root: str = "workspace"


@router.put("/file")
async def write_file(req: FileWriteRequest) -> Dict[str, Any]:
    """写入（新建或覆盖）文本文件。"""
    try:
        return await asyncio.to_thread(_svc.write_file, req.path, req.content, req.root)
    except WorkspaceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


class MkdirRequest(BaseModel):
    path: str
    root: str = "workspace"


@router.post("/mkdir")
async def make_dir(req: MkdirRequest) -> Dict[str, Any]:
    try:
        return await asyncio.to_thread(_svc.make_dir, req.path, req.root)
    except WorkspaceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


class MoveRequest(BaseModel):
    src: str
    dst: str
    root: str = "workspace"


@router.post("/move")
async def move_entry(req: MoveRequest) -> Dict[str, Any]:
    """重命名 / 移动文件或目录（dst 为目标全路径，目标父目录须已存在）。"""
    try:
        return await asyncio.to_thread(_svc.move_entry, req.src, req.dst, req.root)
    except WorkspaceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    dir: str = Query(""),
    root: str = Query("workspace"),
) -> Dict[str, Any]:
    """上传文件到指定目录（multipart，上限 50MB，重名拒绝覆盖）。"""
    try:
        fp = _svc.upload_target_dir(dir, root, file.filename or "")
        return await _svc.save_upload(file, fp, root)
    except WorkspaceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e


@router.delete("/file")
async def delete_file(path: str = Query(...), root: str = Query("workspace")) -> Dict[str, str]:
    try:
        await asyncio.to_thread(_svc.delete_entry, path, root)
    except WorkspaceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    return {"status": "ok"}


@router.get("/search")
async def search_files(q: str = Query(..., min_length=1), limit: int = Query(30, ge=1, le=100)) -> Dict[str, Any]:
    """搜索工作区：文件名匹配 + 文本内容匹配。"""
    # os.walk + 逐文件读取为同步阻塞 I/O，移入线程避免卡住事件循环
    return await asyncio.to_thread(_svc.search_files, q, limit)
