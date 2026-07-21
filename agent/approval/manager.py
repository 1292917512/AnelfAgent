"""批准管理器 — 全局管理所有挂起的批准请求。

职责：
- 维护 request_id → ApprovalSession 映射
- 提供决策接口（approve / deny / cancel）
- 自动清理过期会话
- 决策历史记录（供 trust_after_n_approvals 使用）
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from core.log import log

from .policy import ApprovalPolicy
from .session import ApprovalDecision, ApprovalRequest, ApprovalSession


class ApprovalManager:
    """批准管理器（单例）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, ApprovalSession] = {}
        self._decision_history: List[ApprovalSession] = []  # 最近的决策历史
        self._history_max_size: int = 1000
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

    async def create_session(self, request: ApprovalRequest) -> ApprovalSession:
        """创建新的批准会话。"""
        session = ApprovalSession(request=request)
        async with self._lock:
            self._sessions[request.request_id] = session
        log(
            f"批准会话已创建: {request.request_id} "
            f"({request.tool_name}, risk={request.risk_level.value})",
            tag="批准",
        )
        return session

    async def get_session(self, request_id: str) -> Optional[ApprovalSession]:
        """获取会话。"""
        async with self._lock:
            return self._sessions.get(request_id)

    async def list_pending(self, channel_id: str = "") -> List[ApprovalSession]:
        """列出所有挂起的会话（可按频道过滤）。"""
        async with self._lock:
            sessions = list(self._sessions.values())
        if channel_id:
            sessions = [s for s in sessions if s.request.requester_channel == channel_id]
        return [s for s in sessions if s.is_pending()]

    # ------------------------------------------------------------------
    # 决策
    # ------------------------------------------------------------------

    async def resolve(
        self,
        request_id: str,
        decision: ApprovalDecision,
        decided_by: str = "",
        reason: str = "",
    ) -> bool:
        """标记会话为已决策。"""
        async with self._lock:
            session = self._sessions.get(request_id)
            if not session or not session.is_pending():
                return False
            session.resolve(decision, decided_by, reason)
            # 移入历史
            self._decision_history.append(session)
            if len(self._decision_history) > self._history_max_size:
                self._decision_history.pop(0)
        log(
            f"批准会话已决策: {request_id} -> {decision.value} "
            f"(by {decided_by or 'system'})",
            tag="批准",
        )
        return True

    async def approve(self, request_id: str, decided_by: str = "", reason: str = "") -> bool:
        """批准。"""
        return await self.resolve(request_id, ApprovalDecision.APPROVED, decided_by, reason)

    async def deny(self, request_id: str, decided_by: str = "", reason: str = "") -> bool:
        """拒绝。"""
        return await self.resolve(request_id, ApprovalDecision.DENIED, decided_by, reason)

    async def cancel(self, request_id: str, reason: str = "") -> bool:
        """取消（如 agent 中断）。"""
        return await self.resolve(request_id, ApprovalDecision.CANCELLED, "system", reason)

    # ------------------------------------------------------------------
    # 历史与信任
    # ------------------------------------------------------------------

    async def get_recent_approvals_for_tool(
        self,
        tool_name: str,
        user_id: str,
        limit: int = 10,
    ) -> List[ApprovalSession]:
        """获取某用户对某工具的最近批准历史。"""
        async with self._lock:
            history = [
                s for s in self._decision_history
                if s.request.tool_name == tool_name
                and s.request.requester_user_id == user_id
                and s.decision == ApprovalDecision.APPROVED
            ]
        return history[-limit:]

    async def is_trusted(
        self,
        tool_name: str,
        user_id: str,
        policy: ApprovalPolicy,
    ) -> bool:
        """检查是否已达到 trust_after_n_approvals 阈值。"""
        if policy.trust_after_n_approvals <= 0:
            return False
        recent = await self.get_recent_approvals_for_tool(
            tool_name, user_id, limit=policy.trust_after_n_approvals,
        )
        return len(recent) >= policy.trust_after_n_approvals

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    async def cleanup_expired(self) -> int:
        """清理过期会话，返回清理数量。"""
        async with self._lock:
            expired = [
                rid for rid, s in self._sessions.items()
                if s.is_expired() and s.status == "pending"
            ]
            for rid in expired:
                session = self._sessions.pop(rid)
                session.resolve(ApprovalDecision.EXPIRED, "system", "timeout")
                self._decision_history.append(session)
        if expired:
            log(f"批准会话清理: {len(expired)} 个过期会话已标记", "DEBUG", tag="批准")
        return len(expired)

    async def start_cleanup_task(self, interval: float = 30.0) -> None:
        """启动后台清理任务。"""
        if self._cleanup_task and not self._cleanup_task.done():
            return

        async def _loop():
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.cleanup_expired()
                except Exception as exc:
                    log(f"批准会话清理异常: {exc}", "WARNING", tag="批准")

        self._cleanup_task = asyncio.create_task(_loop(), name="approval.cleanup")

    async def stop_cleanup_task(self) -> None:
        """停止清理任务。"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        self._cleanup_task = None

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息。"""
        async with self._lock:
            pending = sum(1 for s in self._sessions.values() if s.is_pending())
            history_by_decision: Dict[str, int] = {}
            for s in self._decision_history:
                key = s.decision.value if s.decision else "unknown"
                history_by_decision[key] = history_by_decision.get(key, 0) + 1
        return {
            "pending_count": pending,
            "history_size": len(self._decision_history),
            "history_by_decision": history_by_decision,
        }


# ======================================================================
# 全局单例
# ======================================================================

_manager: Optional[ApprovalManager] = None


def get_approval_manager() -> ApprovalManager:
    """获取全局批准管理器。"""
    global _manager
    if _manager is None:
        _manager = ApprovalManager()
    return _manager
