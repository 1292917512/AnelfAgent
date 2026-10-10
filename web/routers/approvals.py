"""权限规则与审计 API。"""

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from services import ApprovalService

router = APIRouter(prefix="/approvals", tags=["permissions"])
_service = ApprovalService()


@router.get("/history")
async def list_history(
    limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), tool_name: str = Query(""),
) -> dict[str, Any]:
    """按时间倒序读取审计记录。"""
    return {"history": await _service.list_history(limit, offset, tool_name), "offset": offset, "limit": limit}


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """获取裁决结果分布。"""
    return await _service.get_stats()


@router.get("/rules")
async def get_rules() -> dict[str, Any]:
    """获取权限规则。"""
    return _service.get_rules()


class RuleSetUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: list[dict[str, Any]]
    default_effect: Literal["allow", "ask", "deny"] = "allow"


@router.put("/rules")
async def save_rule_set(data: RuleSetUpdateRequest) -> dict[str, Any]:
    """校验、持久化并应用权限规则。"""
    try:
        count = _service.save_rule_set(data.rules, data.default_effect)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(400, f"Invalid permission rules: {exc}") from exc
    return {"status": "ok", "count": count}
