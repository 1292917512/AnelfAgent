"""人脸识别 API 路由 — 人脸页签的数据面（/api/face）。

人物档案管理、实体绑定、出现事件时间线、上传识别/注册/对比。
错误映射照音频路由纪律：FaceEngineNotConfigured→503、引擎调用失败→502、
参数/状态错误（ValueError）→400/422、人物或事件不存在→404。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.responses import FileResponse

from services.vision import (
    FaceEngineError,
    FaceEngineNotConfigured,
    FaceServiceFacade,
)

router = APIRouter(prefix="/face", tags=["face"])

_face = FaceServiceFacade()


class PersonUpdateRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    threshold: Optional[float] = None


class PersonBindRequest(BaseModel):
    entity_scope: str = ""


class ConfirmRequest(BaseModel):
    name: str
    role: str = ""


class MergeRequest(BaseModel):
    source_id: int
    target_id: int


class MarkReadRequest(BaseModel):
    event_ids: Optional[List[int]] = None
    read: bool = True


@router.get("/status")
async def status(refresh: bool = False) -> Dict[str, Any]:
    """识别引擎状态 + 库统计 + 生效阈值（refresh 重置探测缓存）。"""
    return await _face.status(refresh=refresh)


@router.post("/engine/unload")
async def engine_unload() -> Dict[str, Any]:
    """识别引擎模型层显存释放（进程常驻，下次推理自动重载）。"""
    try:
        return await _face.engine_unload()
    except FaceEngineNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except FaceEngineError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/stats")
async def stats() -> Dict[str, Any]:
    """人脸库总览统计。"""
    return await _face.stats()


# ── 人物身份 ──────────────────────────────────────────────────────


@router.get("/persons")
async def list_persons(
    status: str = "", keyword: str = "", limit: int = 50, offset: int = 0,
) -> Dict[str, Any]:
    return await _face.list_persons(status=status, keyword=keyword,
                                    limit=limit, offset=offset)


@router.get("/persons/by-entity/{entity_scope:path}")
async def persons_by_entity(entity_scope: str) -> Dict[str, Any]:
    """反向查询：实体画像绑定的全部人脸身份。"""
    return {"items": await _face.persons_for_entity(entity_scope)}


@router.get("/persons/{person_id}")
async def get_person(person_id: int) -> Dict[str, Any]:
    detail = await _face.person_detail(person_id)
    if detail is None:
        raise HTTPException(404, "人物不存在")
    return detail


@router.patch("/persons/{person_id}")
async def update_person(person_id: int, req: PersonUpdateRequest) -> Dict[str, Any]:
    updated = await _face.update_person(person_id, **req.model_dump())
    if updated is None:
        raise HTTPException(404, "人物不存在")
    return {"person": updated}


@router.post("/persons/{person_id}/bind")
async def bind_person(person_id: int, req: PersonBindRequest) -> Dict[str, Any]:
    try:
        updated = await _face.bind_person(person_id, req.entity_scope)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "人物不存在")
    return {"person": updated}


@router.post("/persons/{person_id}/confirm")
async def confirm_person(person_id: int, req: ConfirmRequest) -> Dict[str, Any]:
    confirmed = await _face.confirm_person(person_id, req.name, role=req.role)
    if confirmed is None:
        raise HTTPException(404, "人物不存在")
    return {"person": confirmed}


@router.post("/persons/{person_id}/refine")
async def refine_person(person_id: int) -> Dict[str, Any]:
    try:
        return await _face.refine_person(person_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/persons/{person_id}")
async def delete_person(person_id: int) -> Dict[str, Any]:
    deleted = await _face.delete_person(person_id)
    if deleted is None:
        raise HTTPException(404, "人物不存在")
    return {"deleted": deleted}


@router.post("/persons/merge")
async def merge_persons(req: MergeRequest) -> Dict[str, Any]:
    try:
        return await _face.merge_persons(req.source_id, req.target_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/persons/prune")
async def prune_persons(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    return await _face.prune_persons(bool(payload.get("include_with_samples", False)))


@router.post("/persons/consolidate")
async def consolidate_persons(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    return await _face.consolidate_persons(payload)


@router.delete("/samples/{sample_id}")
async def delete_sample(sample_id: int) -> Dict[str, Any]:
    ok = await _face.delete_sample(sample_id)
    if not ok:
        raise HTTPException(404, "样本不存在")
    return {"deleted": sample_id}


# ── 出现事件 ──────────────────────────────────────────────────────


@router.get("/events")
async def list_events(
    person_id: Optional[int] = None, entity_scope: str = "", source: str = "",
    from_ns: Optional[int] = None, to_ns: Optional[int] = None,
    unread_only: bool = False, limit: int = 20, offset: int = 0,
) -> Dict[str, Any]:
    return await _face.list_events(
        person_id=person_id, entity_scope=entity_scope, source=source,
        from_ns=from_ns, to_ns=to_ns, unread_only=unread_only,
        limit=limit, offset=offset)


@router.post("/events/mark-read")
async def mark_read(req: MarkReadRequest) -> Dict[str, Any]:
    affected = await _face.mark_read(req.event_ids, read=req.read)
    return {"affected": affected}


@router.delete("/events/{event_id}")
async def delete_event(event_id: int) -> Dict[str, Any]:
    ok = await _face.delete_event(event_id)
    if not ok:
        raise HTTPException(404, "事件不存在")
    return {"deleted": event_id}


# ── 识别 / 注册 / 对比（上传图片）─────────────────────────────────


@router.post("/identify")
async def identify_image(
    file: UploadFile = File(...),
    ingest: bool = Form(default=False),
) -> Dict[str, Any]:
    """上传识别人脸：ingest=true 入库（事件+样本+建档），否则仅返回候选预览。"""
    try:
        return await _face.identify_image(
            file.filename or "", await file.read(), ingest=ingest)
    except FaceEngineNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except FaceEngineError as exc:
        raise HTTPException(502 if exc.retryable else 422, str(exc)) from exc


@router.post("/enroll")
async def enroll_image(
    file: UploadFile = File(...),
    name: str = Form(...),
    face_index: int = Form(default=0),
    role: str = Form(default=""),
    notes: str = Form(default=""),
    entity_scope: str = Form(default=""),
) -> Dict[str, Any]:
    """上传注册人物人脸（同名已确认档案则累积样本）。"""
    try:
        return await _face.enroll_image(
            file.filename or "", await file.read(), name,
            face_index=face_index, role=role, notes=notes, entity_scope=entity_scope)
    except FaceEngineNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except FaceEngineError as exc:
        raise HTTPException(502 if exc.retryable else 422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/compare")
async def compare_images(
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
) -> Dict[str, Any]:
    """上传两张图片对比人脸是否同一人。"""
    try:
        return await _face.compare_images(
            file_a.filename or "", await file_a.read(),
            file_b.filename or "", await file_b.read())
    except FaceEngineNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except FaceEngineError as exc:
        raise HTTPException(502 if exc.retryable else 422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ── 图片预览 ──────────────────────────────────────────────────────

_IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".png": "image/png", ".webp": "image/webp", ".bmp": "image/bmp"}


@router.get("/image")
async def face_image(path: str) -> FileResponse:
    """事件/样本图片预览（仅限 uploads 目录内的文件，防路径穿越）。"""
    import os
    try:
        resolved = _face.resolve_image(path)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    mime = _IMAGE_MIME.get(os.path.splitext(resolved)[1].lower(), "image/jpeg")
    return FileResponse(resolved, media_type=mime)
