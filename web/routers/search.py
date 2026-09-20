"""全局搜索 API 路由 — 聚合记忆、日志、工作区文件、会话记录的统一搜索。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query

from services import SearchService

router = APIRouter(prefix="/search", tags=["search"])

_svc = SearchService()


@router.get("/global")
async def global_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    """全局搜索：聚合记忆 / 日志 / 工作区文件 / 会话记录。"""
    return await _svc.global_search(q, limit)
