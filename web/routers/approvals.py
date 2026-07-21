"""批准机制管理 API 路由。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.approval import get_approval_gate, get_approval_manager

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalDecisionRequest(BaseModel):
    reason: str = ""


@router.get("/pending")
async def list_pending() -> Dict[str, Any]:
    """列出所有待批准的请求。"""
    manager = get_approval_manager()
    pending = await manager.list_pending()
    return {
        "pending": [
            {
                "request_id": s.request.request_id,
                "tool_name": s.request.tool_name,
                "tool_args": s.request.tool_args,
                "risk_level": s.request.risk_level.value,
                "reason": s.request.reason,
                "requester_channel": s.request.requester_channel,
                "requester_chat_id": s.request.requester_chat_id,
                "requester_user_id": s.request.requester_user_id,
                "expires_at": s.request.expires_at,
                "created_at": s.request.created_at,
            }
            for s in pending
        ],
    }


@router.get("/history")
async def list_history(limit: int = 50) -> Dict[str, Any]:
    """列出历史决策记录。"""
    manager = get_approval_manager()
    history = manager._decision_history[-limit:]
    return {
        "history": [
            {
                "request_id": s.request.request_id,
                "tool_name": s.request.tool_name,
                "risk_level": s.request.risk_level.value,
                "decision": s.decision.value if s.decision else "unknown",
                "decided_by": s.decided_by or "",
                "decided_at": s.decided_at or 0,
                "decision_reason": s.decision_reason,
                "requester_user_id": s.request.requester_user_id,
                "requester_channel": s.request.requester_channel,
            }
            for s in history
        ],
    }


@router.post("/{request_id}/approve")
async def approve_request(request_id: str, data: ApprovalDecisionRequest) -> Dict[str, str]:
    """批准请求。"""
    gate = get_approval_gate()
    ok = await gate.approve(request_id, decided_by="webui", reason=data.reason)
    if not ok:
        raise HTTPException(404, "Request not found or already resolved")
    return {"status": "ok"}


@router.post("/{request_id}/deny")
async def deny_request(request_id: str, data: ApprovalDecisionRequest) -> Dict[str, str]:
    """拒绝请求。"""
    gate = get_approval_gate()
    ok = await gate.deny(request_id, decided_by="webui", reason=data.reason)
    if not ok:
        raise HTTPException(404, "Request not found or already resolved")
    return {"status": "ok"}


@router.get("/stats")
async def get_stats() -> Dict[str, Any]:
    """获取统计信息。"""
    manager = get_approval_manager()
    stats = await manager.get_stats()
    return stats


@router.get("/policies")
async def get_policies() -> Dict[str, Any]:
    """获取当前策略集。"""
    gate = get_approval_gate()
    policy_set = gate.get_policy_set()
    return {
        "policies": [
            {
                "tool_name_pattern": p.tool_name_pattern,
                "risk_level": p.risk_level.value,
                "requires_approval": p.requires_approval,
                "timeout_seconds": p.timeout_seconds,
                "on_timeout": p.on_timeout,
                "trust_after_n_approvals": p.trust_after_n_approvals,
                "auto_approve_users": p.auto_approve_users,
                "auto_deny_users": p.auto_deny_users,
                "description": p.description,
            }
            for p in policy_set.policies
        ],
    }


class PolicyUpdateRequest(BaseModel):
    policies: List[Dict[str, Any]]


@router.put("/policies")
async def save_policies(data: PolicyUpdateRequest) -> Dict[str, str]:
    """保存策略集（触发热更新）。"""
    from agent.approval.policy import ApprovalPolicy, ApprovalPolicySet

    try:
        policies = [ApprovalPolicy(**p) for p in data.policies]
        policy_set = ApprovalPolicySet(policies=policies)

        gate = get_approval_gate()
        gate.set_policy_set(policy_set)

        # 保存到文件（触发 ConfigWatcher 自动重载）
        import json
        import os
        policies_path = "config/approval_policies.json"
        os.makedirs(os.path.dirname(policies_path), exist_ok=True)
        with open(policies_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "policies": [
                        {
                            "tool_name_pattern": p.tool_name_pattern,
                            "risk_level": p.risk_level.value,
                            "requires_approval": p.requires_approval,
                            "timeout_seconds": p.timeout_seconds,
                            "on_timeout": p.on_timeout,
                            "trust_after_n_approvals": p.trust_after_n_approvals,
                            "auto_approve_users": p.auto_approve_users,
                            "auto_deny_users": p.auto_deny_users,
                            "description": p.description,
                        }
                        for p in policies
                    ],
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        return {"status": "ok", "count": len(policies)}
    except Exception as exc:
        raise HTTPException(400, f"Invalid policy data: {exc}")
