"""频道/适配器管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import AdapterService
from web.routers._errors import server_error

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


@router.post("/reload")
async def reload_channels() -> Dict[str, Any]:
    """热同步频道目录：新增热插入 / 消失热拔除 / 存续代码热重载。"""
    try:
        return await _adapter_svc.reload_channels()
    except Exception as e:
        raise server_error("热同步频道", e) from e


@router.put("/{key}/toggle")
async def toggle_adapter(key: str) -> Dict[str, str]:
    try:
        await _adapter_svc.toggle_adapter(key)
        return {"status": "ok"}
    except Exception as e:
        raise server_error("切换频道", e) from e


@router.post("/{key}/reload")
async def reload_channel(key: str) -> Dict[str, Any]:
    """按实际频道目录定向重载，不重连其他频道；失败保留可管理的实例。"""
    try:
        result = await _adapter_svc.reload_channels(key)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise server_error("重载频道", exc) from exc
    if result.get("skipped"):
        raise HTTPException(409, "频道同步正在进行，请稍后重试")
    if result.get("failed"):
        raise HTTPException(500, "频道重载失败，请检查频道状态与运行日志")
    return result


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
        raise HTTPException(404, str(e)) from e


@router.post("/{key}/tools/{name}/test")
async def test_channel_tool(key: str, name: str, body: ToolTestRequest) -> Dict[str, Any]:
    return await _adapter_svc.test_channel_tool(key, name, body.args)
