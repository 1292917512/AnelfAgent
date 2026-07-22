"""全局搜索 API 路由 — 聚合记忆、日志、工作区文件、会话记录的统一搜索。"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from fastapi import APIRouter, Query

from core.log import log, query_log_buffer

router = APIRouter(prefix="/search", tags=["search"])


async def _search_memory(q: str, limit: int) -> List[Dict[str, Any]]:
    """搜索长期记忆（复用记忆服务的混合检索）。"""
    try:
        from services import MemoryService
        results = await MemoryService().search_ltm(query=q, limit=limit)
        return [
            {
                "id": r.get("id"), "snippet": r.get("snippet", ""),
                "memory_type": r.get("memory_type", ""), "tags": r.get("tags", []),
                "score": r.get("score", 0),
            }
            for r in results
        ]
    except Exception as e:
        log(f"全局搜索记忆失败: {e}", "DEBUG")
        return []


async def _search_conversations(q: str, limit: int) -> List[Dict[str, Any]]:
    """跨 scope 搜索会话消息。"""
    try:
        from services._runtime import get_runtime
        rt = get_runtime()
        if rt is None:
            return []
        rows = await rt.data_center.sqlite.search_conversation_global(q, limit=limit)
        results: List[Dict[str, Any]] = []
        for r in rows:
            ts_ns = r.get("ts_ns") or 0
            ts = ts_ns / 1e9 if ts_ns > 1e15 else ts_ns
            results.append({
                "id": r.get("id"),
                "scope": f"{r.get('scope_type')}:{r.get('scope_id')}",
                "role": r.get("role", ""),
                "snippet": str(r.get("content", ""))[:200],
                "time": time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "",
            })
        return results
    except Exception as e:
        log(f"全局搜索会话失败: {e}", "DEBUG")
        return []


@router.get("/global")
async def global_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    """全局搜索：聚合记忆 / 日志 / 工作区文件 / 会话记录。"""
    from web.routers.workspace import search_workspace

    memory = await _search_memory(q, limit)
    conversations = await _search_conversations(q, limit)
    logs = query_log_buffer(keyword=q, limit=limit)
    files = search_workspace(q, limit=limit)

    return {
        "query": q,
        "memory": memory,
        "logs": logs,
        "files": files,
        "conversations": conversations,
    }
