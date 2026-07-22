"""频道/适配器管理 API 路由。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import AdapterService

router = APIRouter(prefix="/adapters", tags=["adapters"])

_adapter_svc = AdapterService()


class TestSendRequest(BaseModel):
    """频道测试消息请求。"""

    chat_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=4000)


class ToolTestRequest(BaseModel):
    """频道接口调用测试请求。"""

    args: Dict[str, Any] = Field(default_factory=dict)


@router.get("/")
async def list_adapters() -> Dict[str, Any]:
    adapters = _adapter_svc.list_adapters()
    if adapters is None:
        return {"ready": False, "adapters": []}
    return {"ready": True, "adapters": adapters}


@router.put("/{key}/toggle")
async def toggle_adapter(key: str) -> Dict[str, str]:
    loop = asyncio.get_running_loop()
    try:
        _adapter_svc.toggle_adapter(key, loop)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/{key}/test/health")
async def test_channel_health(key: str) -> Dict[str, Any]:
    return await _adapter_svc.test_channel_health(key)


@router.post("/{key}/test/send")
async def test_channel_send(key: str, body: TestSendRequest) -> Dict[str, Any]:
    return await _adapter_svc.test_channel_send(key, body.chat_id, body.text)


@router.get("/{key}/tools")
async def list_channel_tools(key: str) -> Dict[str, Any]:
    return _adapter_svc.get_channel_tools(key)


@router.put("/{key}/tools/{name}/toggle")
async def toggle_channel_tool(key: str, name: str) -> Dict[str, Any]:
    try:
        return _adapter_svc.toggle_channel_tool(key, name)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/{key}/tools/{name}/test")
async def test_channel_tool(key: str, name: str, body: ToolTestRequest) -> Dict[str, Any]:
    return await _adapter_svc.test_channel_tool(key, name, body.args)


@router.get("/configs")
async def get_configs() -> Dict[str, Dict[str, Any]]:
    return _adapter_svc.get_adapter_configs()


@router.put("/configs")
async def save_configs(values: Dict[str, Any]) -> Dict[str, int]:
    changed = _adapter_svc.save_adapter_configs(values)

    # 触发热更新：重载所有频道配置
    if changed > 0:
        from agent.channel import get_channel_manager
        manager = get_channel_manager()
        for channel in manager.list_channels().values():
            try:
                channel.reload_config()
            except Exception:
                pass  # 单个频道重载失败不影响其他频道

    return {"changed": changed}
