"""智能体状态 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from services import AgentStatusService, is_ready

router = APIRouter(prefix="/status", tags=["status"])

_status_svc = AgentStatusService()


@router.get("/")
async def get_status() -> Dict[str, Any]:
    if not is_ready():
        return {"ready": False}
    status = _status_svc.get_status()
    return {"ready": True, "status": status}


@router.get("/components")
async def get_components() -> Dict[str, Any]:
    lines = _status_svc.get_component_info()
    return {"lines": lines}


@router.get("/events")
async def get_event_stats() -> Dict[str, Any]:
    stats = _status_svc.get_event_stats()
    return {"stats": stats or {}}


@router.get("/mind-config")
async def get_mind_config() -> Dict[str, Any]:
    config = _status_svc.get_mind_config()
    return {"config": config or {}}


from web.routers.schemas import MindConfigUpdate


@router.put("/mind-config")
async def save_mind_config(data: MindConfigUpdate) -> Dict[str, str]:
    params = {k: v for k, v in data.model_dump().items() if v is not None}
    _status_svc.save_mind_config(params)
    return {"status": "ok"}


@router.get("/pfc")
async def get_pfc_status() -> Dict[str, Any]:
    """PFC 工作记忆状态快照。"""
    snapshot = _status_svc.get_pfc_snapshot()
    return snapshot or {}


@router.get("/logs")
async def get_logs(
    level: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
) -> Dict[str, Any]:
    from core.log import query_log_buffer
    logs = query_log_buffer(level=level, tag=tag, keyword=keyword, limit=limit)
    return {"logs": logs, "count": len(logs)}


@router.post("/logs/clear")
async def clear_logs() -> Dict[str, Any]:
    """清空内存日志缓冲区。"""
    from core.log import clear_log_buffer
    cleared = clear_log_buffer()
    return {"status": "ok", "cleared": cleared}


@router.get("/log-stats")
async def get_log_stats() -> Dict[str, Any]:
    from core.log import get_log_buffer_stats
    return get_log_buffer_stats()


@router.get("/logs/stream")
async def log_stream(request: Request) -> EventSourceResponse:
    """SSE: 实时推送新日志条目。"""
    import asyncio
    import json
    import time as _time
    from core.log import add_listener, remove_listener

    queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=256)

    def on_log(data: Dict[str, Any]) -> None:
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            pass

    add_listener(on_log)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=30.0)
                    payload = {
                        "level": entry.get("level", "INFO"),
                        "message": entry.get("message", ""),
                        "tag": entry.get("tag", ""),
                        "time": _time.strftime("%H:%M:%S", _time.localtime(entry.get("timestamp", _time.time()))),
                    }
                    yield {"event": "log", "data": json.dumps(payload, ensure_ascii=False)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            remove_listener(on_log)

    return EventSourceResponse(event_generator())
