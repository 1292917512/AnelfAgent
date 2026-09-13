"""智能家居实体的 HTTP 路由（自动挂载到 /api/entity/smart_home）。

经 web/server.py 的 _mount_entity_routers 扫描发现。
- 连接状态 / 设备清单 / 设备域组件 / 注入预览
- 设备控制（面板控制按钮）
- /stream SSE 实时推送设备状态变更（前端面板实时刷新）

配置读写（连接 url/token、域启停等）复用通用实体配置端点
``PUT /api/entities/smart_home/config``，本路由只提供实体专属操作。
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from core.tool_errors import ErrorCause

from . import framework
from .manager import get_smart_home_manager
from .models import SmartHomeCallError
from .schemas import ControlRequest, DevicesResult, StatusResult

# 错误归因 → HTTP 状态码（面板按语义展示）
_CAUSE_STATUS = {
    ErrorCause.PARAM: 400,
    ErrorCause.NOT_FOUND: 404,
    ErrorCause.CONFIG: 400,
    ErrorCause.STATE: 409,
    ErrorCause.PERMISSION: 403,
    ErrorCause.NETWORK: 502,
    ErrorCause.TIMEOUT: 504,
}


def build_router() -> APIRouter:
    """构建智能家居路由（被 web 层挂载到 /api/entity/smart_home）。"""
    router = APIRouter()

    @router.get("/status", response_model=StatusResult)
    async def get_status() -> dict:
        """平台连接状态（配置齐备/已连接/设备计数/最近错误）。"""
        return get_smart_home_manager().status()

    @router.get("/devices", response_model=DevicesResult)
    async def list_devices(domain: str = "", area: str = "", name: str = "") -> dict:
        """设备全量清单与实时状态（可按设备域/房间/名称过滤）。"""
        manager = get_smart_home_manager()
        devices = [
            d.to_dict()
            for d in manager.devices(domain=domain, area=area, name=name)
        ]
        return {
            "connection": manager.status(),
            "devices": devices,
            "count": len(devices),
        }

    @router.get("/domains")
    async def list_domains() -> dict:
        """全部设备域组件的状态与配置描述（面板数据驱动渲染）。"""
        manager = get_smart_home_manager()
        devices = manager.devices()
        domains = [d.describe(devices) for d in framework.all_domains()]
        return {"domains": domains, "count": len(domains)}

    @router.get("/preview")
    async def preview_context() -> dict:
        """当前注入 AI 上下文的聚合文本预览。"""
        manager = get_smart_home_manager()
        content = framework.render_context(manager.devices())
        return {"injecting": bool(content), "content": content}

    @router.post("/control")
    async def control_device(req: ControlRequest) -> dict:
        """控制设备（面板控制按钮；动作经设备域校验）。"""
        manager = get_smart_home_manager()
        try:
            result = await manager.call_service(req.entity_id, req.action, req.value)
        except SmartHomeCallError as exc:
            raise HTTPException(
                status_code=_CAUSE_STATUS.get(exc.cause, 500),
                detail=str(exc),
            ) from exc
        return {"success": True, **result}

    @router.get("/stream")
    async def event_stream(request: Request) -> EventSourceResponse:
        """SSE 推送设备状态变更/连接状态（前端 EventSource 订阅）。"""
        manager = get_smart_home_manager()
        queue = manager.subscribe()

        async def event_generator():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield {
                            "event": msg.get("event", "state"),
                            "data": json.dumps(msg, ensure_ascii=False),
                        }
                    except asyncio.TimeoutError:
                        yield {"event": "ping", "data": ""}
            finally:
                manager.unsubscribe(queue)

        return EventSourceResponse(event_generator())

    return router
