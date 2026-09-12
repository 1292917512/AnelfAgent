"""子代理委托全局总览 API — Dashboard「子代理」面板数据源。

与 /chat/delegations（单会话卡片恢复）分工：本路由提供全 scope 运行快照、
执行历史（账本）、进度流读取与面板操作（转向/取消）。操作反馈经
DelegationService 汇入 manager 既有闭环（SteerInbox / 注册表完成通知）。
"""

from typing import Any, Dict

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from services import DelegationService

router = APIRouter(prefix="/delegations", tags=["delegations"])

_delegation_svc = DelegationService()


@router.get("/overview")
async def overview() -> Dict[str, Any]:
    """全 scope 运行中委托快照（含实时进度与用量）。"""
    return _delegation_svc.overview()


@router.get("/history")
async def history(limit: int = Query(20, ge=1, le=100)) -> Dict[str, Any]:
    """近期委托执行历史（账本配对折叠，按结束时间倒序）。"""
    return _delegation_svc.history(limit)


@router.get("/{delegation_id}/progress")
async def progress(
    delegation_id: str,
    tail: int = Query(200, ge=1, le=1000),
) -> Dict[str, Any]:
    """委托进度流尾部行 + 运行状态（面板进度 Drawer 轮询）。"""
    return _delegation_svc.progress(delegation_id, tail)


class SteerRequest(BaseModel):
    message: str = Field(min_length=1)
    mode: str = "steer"


@router.post("/{delegation_id}/steer")
async def steer(delegation_id: str, req: SteerRequest) -> Dict[str, Any]:
    """向运行中的委托发送转向指令（steer 步骤边界 / after 收束边界）。"""
    result = _delegation_svc.steer(delegation_id, req.message, req.mode)
    if "error" in result:
        return {"status": "error", **result}
    return {"status": "ok", **result}


@router.post("/{delegation_id}/cancel")
async def cancel(delegation_id: str) -> Dict[str, Any]:
    """取消运行中的委托（级联取消后代，反馈经既有闭环回馈父 AI）。"""
    ok = _delegation_svc.cancel(delegation_id)
    if ok is None:
        return {"status": "error", "error": "runtime 未就绪"}
    if not ok:
        return {"status": "error", "error": "委托不存在或已结束"}
    return {"status": "ok"}
