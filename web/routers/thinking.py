"""思维链路追踪 API 路由。"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from core.tracer import thinking_tracer

router = APIRouter(prefix="/thinking", tags=["thinking"])


@router.get("/status")
async def get_tracer_status() -> Dict[str, Any]:
    return thinking_tracer.get_status()


class ToggleRequest(BaseModel):
    enabled: bool


@router.put("/toggle")
async def toggle_tracer(body: ToggleRequest) -> Dict[str, Any]:
    thinking_tracer.set_enabled(body.enabled)
    return {"enabled": thinking_tracer.enabled}


@router.get("/sessions")
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    sessions = thinking_tracer.get_sessions_list()[:limit]
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> Dict[str, Any]:
    data = thinking_tracer.get_session(session_id)
    if data is None:
        return {"error": "session not found"}
    return data


@router.get("/system-nodes")
async def list_system_nodes(
    limit: int = Query(50, ge=1, le=200),
) -> Dict[str, Any]:
    """返回 Mind 会话外产生的系统级追踪节点（代理生命周期、实体调用、适配器事件、错误等）。"""
    nodes = thinking_tracer.get_system_nodes(limit=limit)
    return {"nodes": nodes, "count": len(nodes)}


@router.get("/stream")
async def thinking_stream(request: Request) -> EventSourceResponse:
    """SSE: 实时推送思维链路事件。"""
    queue = thinking_tracer.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=30.0)
                    event_type = payload.get("event", "update")
                    data = payload.get("data", {})
                    yield {
                        "event": event_type,
                        "data": json.dumps(data, ensure_ascii=False, default=str),
                    }
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            thinking_tracer.unsubscribe(queue)

    return EventSourceResponse(event_generator())
