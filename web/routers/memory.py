"""记忆管理 API 路由 — 状态统计 / LTM / STM / 会话 / 实体 / 便签 / 目标 / 文件索引。"""

from __future__ import annotations

import functools
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from services import MemoryService
from web.routers.schemas import (
    CogneeBackfillRequest,
    CogneeConfigUpdate,
    CogneeImproveRequest,
)

router = APIRouter(prefix="/memory", tags=["memory"])

_mem_svc = MemoryService()

# 运行时未初始化（RuntimeError）的统一兜底响应
_FALLBACK_ERROR: Dict[str, Any] = {"error": "运行时未初始化"}
_FALLBACK_LIST: List[Any] = []


def _runtime_fallback(fallback: Any) -> Callable:
    """RuntimeError（运行时未初始化）→ 静态兜底响应；fallback 可为值或与端点同签名的 callable。"""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except RuntimeError:
                return fallback(*args, **kwargs) if callable(fallback) else fallback
        return wrapper
    return decorator


# ── 状态与统计 ─────────────────────────────────────────────────────

@router.get("/health")
@_runtime_fallback(_FALLBACK_ERROR)
async def get_health_status() -> Dict[str, Any]:
    return await _mem_svc.get_health_status()


@router.get("/cognee/status")
async def get_cognee_status() -> Dict[str, Any]:
    return await _mem_svc.get_cognee_status()


@router.get("/cognee/config")
async def get_cognee_config() -> Dict[str, Any]:
    return _mem_svc.get_cognee_config()


@router.put("/cognee/config")
async def save_cognee_config(req: CogneeConfigUpdate) -> Dict[str, Any]:
    return await _mem_svc.save_cognee_config(
        req.model_dump(exclude_none=True),
    )


@router.post("/cognee/retry")
async def retry_cognee() -> Dict[str, Any]:
    return {"retried": await _mem_svc.retry_cognee_sync()}


@router.post("/cognee/rebuild")
async def rebuild_cognee() -> Dict[str, Any]:
    """清空 cognee 数据并全量重建（切换 cognee embedding 模型后需要）。"""
    return await _mem_svc.rebuild_cognee()


@router.post("/cognee/backfill")
async def backfill_cognee(req: CogneeBackfillRequest) -> Dict[str, Any]:
    return await _mem_svc.backfill_cognee(limit=req.limit, dry_run=req.dry_run)


@router.get("/cognee/datasets")
async def list_cognee_datasets() -> List[Dict[str, Any]]:
    return await _mem_svc.list_cognee_datasets()


@router.post("/cognee/improve")
async def improve_cognee(req: CogneeImproveRequest) -> Any:
    return await _mem_svc.improve_cognee(req.dataset_name)


@router.get("/cognee/graph")
async def get_cognee_graph(
    dataset: Optional[str] = Query(None),
) -> HTMLResponse:
    """渲染 Cognee 官方知识图谱 HTML（同源输出，供前端内嵌/新窗口打开）。"""
    try:
        html = await _mem_svc.get_cognee_graph_html(dataset)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return HTMLResponse(html)


@router.get("/ltm/stats")
@_runtime_fallback({"type_counts": {}, "total": 0})
async def get_ltm_stats() -> Dict[str, Any]:
    return await _mem_svc.get_ltm_stats()


@router.get("/stm/status")
@_runtime_fallback(_FALLBACK_LIST)
async def get_pfc_status() -> List[Dict[str, Any]]:
    return _mem_svc.get_pfc_status()


@router.get("/index/status")
@_runtime_fallback(_FALLBACK_ERROR)
async def get_index_status() -> Dict[str, Any]:
    return await _mem_svc.get_index_status()


@router.post("/embedding/rebuild")
@_runtime_fallback(_FALLBACK_ERROR)
async def rebuild_embeddings() -> Dict[str, Any]:
    """清空全部向量数据并按当前配置的域模型后台重建（切换 embedding 模型后调用）。"""
    return await _mem_svc.rebuild_embeddings()

# ── 长期记忆 (LTM) ──────────────────────────────────────────────────

@router.get("/ltm")
@_runtime_fallback(_FALLBACK_LIST)
async def list_ltm(
    memory_type: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> List[Dict[str, Any]]:
    return await _mem_svc.list_ltm(memory_type=memory_type, limit=limit)


@router.get("/ltm/paginated")
@_runtime_fallback(lambda page=1, page_size=50, memory_type=None: {
    "items": [], "total": 0, "page": 1, "page_size": page_size, "pages": 0,
})
async def list_ltm_paginated(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    memory_type: Optional[str] = Query(None),
) -> Dict[str, Any]:
    return await _mem_svc.list_ltm_paginated(page=page, page_size=page_size, memory_type=memory_type)


@router.get("/ltm/search")
@_runtime_fallback(_FALLBACK_LIST)
async def search_ltm(
    query: str = Query(...),
    tags: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
) -> List[Dict[str, Any]]:
    return await _mem_svc.search_ltm(query=query, tags=tags, limit=limit)


@router.get("/ltm/{mem_id}")
async def get_ltm(mem_id: int) -> Dict[str, Any]:
    result = await _mem_svc.get_ltm(mem_id)
    if result is None:
        raise HTTPException(404, "记忆不存在")
    return result


class CreateLTMRequest(BaseModel):
    content: str
    memory_type: str = "semantic"
    importance: float = 0.5
    tags: Optional[list[str]] = None


@router.post("/ltm")
async def create_ltm(req: CreateLTMRequest) -> Dict[str, Any]:
    mem_id = await _mem_svc.create_ltm(req.content, req.memory_type, req.importance, req.tags)
    return {"id": mem_id}


class UpdateLTMRequest(BaseModel):
    content: str
    importance: float = 0.5
    tags: Optional[list[str]] = None


@router.put("/ltm/{mem_id}")
async def update_ltm(mem_id: int, req: UpdateLTMRequest) -> Dict[str, Any]:
    ok = await _mem_svc.update_ltm(mem_id, req.content, req.importance, req.tags)
    return {"ok": ok}


class MergeLTMRequest(BaseModel):
    ids: list[int]
    content: str


@router.post("/ltm/merge")
@_runtime_fallback(_FALLBACK_ERROR)
async def merge_ltm(req: MergeLTMRequest) -> Dict[str, Any]:
    return await _mem_svc.merge_ltm(req.ids, req.content)


@router.delete("/ltm/{mem_id}")
async def delete_ltm(mem_id: int) -> Dict[str, Any]:
    ok = await _mem_svc.delete_ltm(mem_id)
    return {"ok": ok}


@router.delete("/ltm")
async def clear_ltm(
    memory_type: Optional[str] = Query(None),
) -> Dict[str, int]:
    count = await _mem_svc.clear_ltm(memory_type=memory_type)
    return {"cleared": count}

# ── 短期记忆 (STM) ──────────────────────────────────────────────────

@router.get("/stm")
@_runtime_fallback(_FALLBACK_LIST)
async def list_stm() -> List[Dict[str, Any]]:
    return _mem_svc.list_stm()


@router.delete("/stm/{index}")
async def delete_stm(index: int) -> Dict[str, Any]:
    ok = _mem_svc.delete_stm(index)
    return {"ok": ok}


@router.delete("/stm")
async def clear_stm() -> Dict[str, int]:
    count = _mem_svc.clear_stm()
    return {"cleared": count}

# ── 会话记录 ─────────────────────────────────────────────────────────

@router.get("/conversations/scopes")
@_runtime_fallback(_FALLBACK_LIST)
async def list_conv_scopes() -> List[Dict[str, Any]]:
    return await _mem_svc.list_conv_scopes()


@router.get("/conversations")
@_runtime_fallback(_FALLBACK_LIST)
async def list_conv_messages(
    scope_type: str = Query("user"),
    scope_id: str = Query(...),
    limit: int = Query(200, ge=1, le=1000),
) -> List[Dict[str, Any]]:
    return await _mem_svc.list_conv_messages(scope_type, scope_id, limit)


@router.delete("/conversations/{row_id}")
async def delete_conv(row_id: int) -> Dict[str, str]:
    await _mem_svc.delete_conv(row_id)
    return {"status": "ok"}


class ClearConvRequest(BaseModel):
    scope_type: str
    scope_id: str


@router.post("/conversations/clear")
async def clear_conv(req: ClearConvRequest) -> Dict[str, int]:
    count = await _mem_svc.clear_conv(req.scope_type, req.scope_id)
    return {"cleared": count}

# ── 实体画像 ─────────────────────────────────────────────────────────

@router.get("/entities")
@_runtime_fallback(_FALLBACK_LIST)
async def list_entities() -> List[Dict[str, Any]]:
    return await _mem_svc.list_entities()


class SaveEntityRequest(BaseModel):
    scope_type: str
    scope_id: str
    personality: str


@router.put("/entities")
async def save_entity(req: SaveEntityRequest) -> Dict[str, str]:
    await _mem_svc.save_entity(req.scope_type, req.scope_id, req.personality)
    return {"status": "ok"}


class DeleteEntityRequest(BaseModel):
    scope_type: str
    scope_id: str


@router.post("/entities/delete")
async def delete_entity(req: DeleteEntityRequest) -> Dict[str, str]:
    await _mem_svc.delete_entity(req.scope_type, req.scope_id)
    return {"status": "ok"}


@router.get("/entities/aliases")
@_runtime_fallback(_FALLBACK_LIST)
async def list_entity_aliases() -> List[Dict[str, Any]]:
    return await _mem_svc.list_entity_aliases()


class LinkEntityRequest(BaseModel):
    source_scope_type: str
    source_scope_id: str
    target_scope_type: str
    target_scope_id: str


@router.post("/entities/link")
async def link_entity(req: LinkEntityRequest) -> Dict[str, str]:
    await _mem_svc.link_entity(
        req.source_scope_type, req.source_scope_id,
        req.target_scope_type, req.target_scope_id,
    )
    return {"status": "ok"}


class UnlinkEntityRequest(BaseModel):
    scope_type: str
    scope_id: str


@router.post("/entities/unlink")
async def unlink_entity(req: UnlinkEntityRequest) -> Dict[str, Any]:
    ok = await _mem_svc.unlink_entity(req.scope_type, req.scope_id)
    return {"ok": ok}

# ── 便签记忆 ─────────────────────────────────────────────────────────

@router.get("/notes")
async def read_notes() -> Dict[str, str]:
    return {"content": _mem_svc.read_notes(), "path": _mem_svc.get_notes_path()}


class WriteNotesRequest(BaseModel):
    content: str


@router.put("/notes")
async def write_notes(req: WriteNotesRequest) -> Dict[str, str]:
    _mem_svc.write_notes(req.content)
    return {"status": "ok"}


@router.get("/files")
async def list_memory_files() -> List[Dict[str, str]]:
    return _mem_svc.list_memory_files()


@router.get("/files/content")
async def read_memory_file(path: str = Query(...)) -> Dict[str, str]:
    return {"content": _mem_svc.read_memory_file(path)}


class WriteMemoryFileRequest(BaseModel):
    path: str
    content: str


@router.put("/files/content")
async def write_memory_file(req: WriteMemoryFileRequest) -> Dict[str, int]:
    lines = _mem_svc.write_memory_file(req.path, req.content)
    return {"lines": lines}


@router.delete("/files")
async def delete_memory_file(path: str = Query(...)) -> Dict[str, str]:
    try:
        removed = _mem_svc.delete_memory_file(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"{path} 不存在")
    return {"status": "ok"}

# ── 目标计划 ─────────────────────────────────────────────────────────

@router.get("/goals")
@_runtime_fallback(lambda status="all": {"goals": [], "total": 0, "filter": status})
async def list_goals(
    status: str = Query("all", description="筛选状态: active / completed / cancelled / all"),
) -> Dict[str, Any]:
    goals = await _mem_svc.list_goals(status=status)
    return {"goals": goals, "total": len(goals), "filter": status}


@router.get("/goals/{goal_id}")
async def get_goal(goal_id: str) -> Dict[str, Any]:
    result = await _mem_svc.get_goal(goal_id)
    if result is None:
        raise HTTPException(404, "目标不存在")
    return result


class CreateGoalRequest(BaseModel):
    title: str
    description: str = ""
    steps: Optional[List[str]] = None
    due_time: Optional[str] = None
    recurring: bool = False


@router.post("/goals")
async def create_goal(req: CreateGoalRequest) -> Dict[str, Any]:
    try:
        return await _mem_svc.create_goal(req.title, req.description, req.steps, req.due_time, req.recurring)
    except RuntimeError:
        raise HTTPException(503, "运行时未初始化") from None


class UpdateGoalRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None
    due_time: Optional[str] = None
    recurring: Optional[bool] = None


@router.put("/goals/{goal_id}")
async def update_goal(goal_id: str, req: UpdateGoalRequest) -> Dict[str, Any]:
    result = await _mem_svc.update_goal(
        goal_id,
        title=req.title,
        description=req.description,
        status=req.status,
        steps=req.steps,
        due_time=req.due_time,
        recurring=req.recurring,
    )
    if result is None:
        raise HTTPException(404, "目标不存在")
    return result


@router.delete("/goals/{goal_id}")
async def delete_goal(goal_id: str) -> Dict[str, Any]:
    ok = await _mem_svc.delete_goal(goal_id)
    return {"ok": ok}

# ── 文件索引 ─────────────────────────────────────────────────────────

@router.post("/index/resync")
@_runtime_fallback(_FALLBACK_ERROR)
async def resync_files(force: bool = Query(False)) -> Dict[str, Any]:
    return await _mem_svc.resync_files(force=force)


@router.post("/index/clean-cache")
@_runtime_fallback(_FALLBACK_ERROR)
async def clean_embedding_cache() -> Dict[str, Any]:
    return await _mem_svc.clean_embedding_cache()


# ── 文档索引 ─────────────────────────────────────────────────────────

@router.post("/documents/upload")
@_runtime_fallback(_FALLBACK_ERROR)
async def upload_document(file: UploadFile = File(...)) -> Dict[str, Any]:
    content = await file.read()
    return await _mem_svc.upload_document(file.filename or "document", content)


@router.get("/documents")
@_runtime_fallback(_FALLBACK_LIST)
async def list_documents() -> List[Dict[str, Any]]:
    return await _mem_svc.list_documents()


@router.delete("/documents")
@_runtime_fallback(_FALLBACK_ERROR)
async def delete_document(path: str = Query(...)) -> Dict[str, Any]:
    return await _mem_svc.delete_document(path)
