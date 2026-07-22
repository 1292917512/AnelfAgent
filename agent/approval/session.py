"""批准会话 — 表示一次挂起的批准请求。

生命周期：
1. pending: 等待用户决策
2. resolved: 已决策（approved / denied / expired / cancelled）
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from .policy import RiskLevel


class ApprovalDecision(str, Enum):
    """批准决策。"""

    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"      # 超时未响应
    CANCELLED = "cancelled"  # 主动取消（如 agent 中断）


class ApprovalRequest(BaseModel):
    """批准请求。"""

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    tool_name: str = Field(..., description="工具名")
    tool_args: Dict[str, Any] = Field(default_factory=dict, description="工具参数（已脱敏）")
    risk_level: RiskLevel = Field(..., description="风险等级")
    reason: str = Field(default="", description="触发批准的原因")
    requester_channel: str = Field(..., description="来源频道")
    requester_chat_id: str = Field(..., description="来源会话 ID")
    requester_user_id: str = Field(..., description="发起用户 ID")
    expires_at: float = Field(..., description="过期时间戳（秒）")
    matched_rule: str = Field(default="", description="命中的策略规则（决策审计）")
    created_at: float = Field(default_factory=time.time)


class ApprovalSession(BaseModel):
    """批准会话。"""

    request: ApprovalRequest
    status: str = Field(default="pending", description="pending / resolved")
    decision: Optional[ApprovalDecision] = Field(default=None)
    decided_by: Optional[str] = Field(default=None, description="决策用户 ID")
    decided_at: Optional[float] = Field(default=None)
    decision_reason: str = Field(default="", description="决策理由")

    def is_expired(self) -> bool:
        """检查是否已过期。"""
        return time.time() > self.request.expires_at

    def is_pending(self) -> bool:
        """检查是否仍在等待。"""
        return self.status == "pending" and not self.is_expired()

    def resolve(
        self,
        decision: ApprovalDecision,
        decided_by: str = "",
        reason: str = "",
    ) -> None:
        """标记为已决策。"""
        self.status = "resolved"
        self.decision = decision
        self.decided_by = decided_by
        self.decided_at = time.time()
        self.decision_reason = reason
