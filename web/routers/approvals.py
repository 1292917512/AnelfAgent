"""批准机制管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services import ApprovalService

router = APIRouter(prefix="/approvals", tags=["approvals"])

_approval_svc = ApprovalService()


class ApprovalDecisionRequest(BaseModel):
    reason: str = ""
    remember: str = "once"  # once / session（本会话不再询问）/ always（永久放行）


@router.get("/pending")
async def list_pending() -> Dict[str, Any]:
    """列出所有待批准的请求。"""
    return {"pending": await _approval_svc.list_pending()}


@router.get("/history")
async def list_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    tool_name: str = Query(""),
) -> Dict[str, Any]:
    """列出历史决策记录（审计账本，持久化、重启不丢；按时间倒序分页）。"""
    rows = await _approval_svc.list_history(limit, offset, tool_name)
    return {
        "history": rows,
        "offset": offset,
        "limit": limit,
    }


@router.post("/{request_id}/approve")
async def approve_request(request_id: str, data: ApprovalDecisionRequest) -> Dict[str, str]:
    """批准请求（可选记住决策：session=本会话不再询问，always=永久放行）。"""
    ok = await _approval_svc.approve(request_id, reason=data.reason, remember=data.remember)
    if not ok:
        raise HTTPException(404, "Request not found or already resolved")
    return {"status": "ok", "remember": data.remember}


@router.post("/{request_id}/deny")
async def deny_request(request_id: str, data: ApprovalDecisionRequest) -> Dict[str, str]:
    """拒绝请求。"""
    ok = await _approval_svc.deny(request_id, reason=data.reason)
    if not ok:
        raise HTTPException(404, "Request not found or already resolved")
    return {"status": "ok"}


@router.get("/stats")
async def get_stats() -> Dict[str, Any]:
    """获取统计信息。"""
    return await _approval_svc.get_stats()


@router.get("/policies")
async def get_policies() -> Dict[str, Any]:
    """获取当前策略集。"""
    return {"policies": _approval_svc.get_policies()}


class PolicyUpdateRequest(BaseModel):
    policies: List[Dict[str, Any]]


@router.put("/policies")
async def save_policies(data: PolicyUpdateRequest) -> Dict[str, str]:
    """保存策略集（触发热更新）。"""
    try:
        count = _approval_svc.save_policies(data.policies)
        return {"status": "ok", "count": count}
    except Exception as exc:
        raise HTTPException(400, f"Invalid policy data: {exc}") from exc


# ------------------------------------------------------------------
# 统一权限规则 API（新模型：allow/ask/deny + global/频道 scope）
# ------------------------------------------------------------------


@router.get("/rules")
async def get_rules() -> Dict[str, Any]:
    """获取统一权限规则集（含会话级规则标注）。"""
    return _approval_svc.get_rules()


class RuleSetUpdateRequest(BaseModel):
    rules: List[Dict[str, Any]]
    default_effect: str = "allow"


@router.put("/rules")
async def save_rule_set(data: RuleSetUpdateRequest) -> Dict[str, Any]:
    """整体保存规则集（写入 config/permission_rules.json，触发热重载）。"""
    try:
        count = _approval_svc.save_rule_set(data.rules, data.default_effect)
    except Exception as exc:
        raise HTTPException(400, f"Invalid rule data: {exc}") from exc
    return {"status": "ok", "count": count}


class RuleCreateRequest(BaseModel):
    pattern: str
    effect: str
    scope: str = "global"
    users: List[str] = []
    risk_level: str = "medium"
    timeout_seconds: float = 60.0
    on_timeout: str = "deny"
    description: str = ""


@router.post("/rules")
async def add_rule(data: RuleCreateRequest) -> Dict[str, Any]:
    """添加单条规则（持久化）。"""
    try:
        rule_id = _approval_svc.add_rule(
            pattern=data.pattern,
            effect=data.effect,
            scope=data.scope,
            users=data.users,
            risk_level=data.risk_level,
            timeout_seconds=data.timeout_seconds,
            on_timeout=data.on_timeout,
            description=data.description,
        )
    except Exception as exc:
        raise HTTPException(400, f"Invalid rule data: {exc}") from exc
    return {"status": "ok", "rule_id": rule_id}


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str) -> Dict[str, str]:
    """删除规则（先查持久规则，再查会话规则）。"""
    if _approval_svc.delete_rule(rule_id):
        return {"status": "ok"}
    raise HTTPException(404, "Rule not found")
