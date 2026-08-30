"""批准管理器 — 全局管理所有挂起的批准请求。

- 维护 request_id → ApprovalSession 映射，提供决策接口（approve / deny / cancel）
- wait_decision 事件驱动等待：决策到达即唤醒；abort_check 命中收束为 CANCELLED
- 自动清理过期会话；决策审计经 approval.audit 持久化（approval_audit 表），
  信任计数从审计账本统计，重启不清零
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

from core.log import log

from . import audit
from .policy import ApprovalPolicy
from .session import ApprovalDecision, ApprovalRequest, ApprovalSession


class ApprovalManager:
    """批准管理器（单例）。"""

    def __init__(self) -> None:
        self._sessions: Dict[str, ApprovalSession] = {}
        # 决策事件：resolve 时唤醒 wait_decision 等待者
        self._events: Dict[str, asyncio.Event] = {}
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
            self._events[request.request_id] = asyncio.Event()
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
        """标记会话为已决策，唤醒等待者，并把决策写入审计账本。"""
        async with self._lock:
            session = self._sessions.get(request_id)
            if not session or not session.is_pending():
                return False
            session.resolve(decision, decided_by, reason)
            event = self._events.get(request_id)
        if event is not None:
            event.set()
        log(
            f"批准会话已决策: {request_id} -> {decision.value} "
            f"(by {decided_by or 'system'})",
            tag="批准",
        )
        # 审计落库（即发即忘，fail-open：失败不影响决策生效）
        audit.record_decision_bg(
            tool_name=session.request.tool_name,
            outcome=decision.value,
            decided_by=decided_by,
            reason=reason or session.decision_reason,
            channel_id=session.request.requester_channel,
            chat_id=session.request.requester_chat_id,
            user_id=session.request.requester_user_id,
            risk_level=session.request.risk_level.value,
            matched_rule=session.request.matched_rule,
            tool_args=session.request.tool_args,
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
    # 等待（事件驱动，零轮询）
    # ------------------------------------------------------------------

    async def wait_decision(
        self,
        request_id: str,
        timeout: float,
        abort_check: Optional[Callable[[], bool]] = None,
    ) -> ApprovalDecision:
        """等待会话决策，返回最终决策。

        决策到达立即唤醒。abort_check 提供时每秒轮询一次该标志位
        （中断注册表本身无事件机制），命中即收束为 CANCELLED；
        超时收束为 EXPIRED（deny/allow/halt 由 gate 按规则决定）。
        """
        event = self._events.get(request_id)
        if event is None:
            return ApprovalDecision.CANCELLED
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            while True:
                session = await self.get_session(request_id)
                if session is None:
                    return ApprovalDecision.EXPIRED  # 已被清理
                if session.decision is not None:
                    return session.decision
                if not session.is_pending():
                    return ApprovalDecision.EXPIRED
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                slice_ = min(remaining, 1.0) if abort_check is not None else remaining
                try:
                    await asyncio.wait_for(event.wait(), slice_)
                except asyncio.TimeoutError:
                    pass  # 分片到期：回环检查 abort/超时
                if abort_check is not None and abort_check():
                    await self.resolve(
                        request_id, ApprovalDecision.CANCELLED, "system", "interrupted",
                    )
                    return ApprovalDecision.CANCELLED
            await self.resolve(request_id, ApprovalDecision.EXPIRED, "system", "timeout")
            return ApprovalDecision.EXPIRED
        finally:
            self._events.pop(request_id, None)

    # ------------------------------------------------------------------
    # 信任（从审计账本统计，重启不清零）
    # ------------------------------------------------------------------

    async def is_trusted(
        self,
        tool_name: str,
        user_id: str,
        policy: ApprovalPolicy,
    ) -> bool:
        """检查是否已达到 trust_after_n_approvals 阈值。

        计数为审计账本中该 (tool, user) 的累计人工批准（approved）次数；
        信任自动放行（trusted）不计入，避免自动放行自我强化信任。
        """
        if policy.trust_after_n_approvals <= 0:
            return False
        count = await audit.count_approvals(tool_name, user_id)
        return count >= policy.trust_after_n_approvals

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    async def cleanup_expired(self) -> int:
        """清理过期会话（pending 超时）与已决策的完成会话（10 分钟保留期），返回清理数量。

        已决策会话短暂保留供路由层/审计读取决策结果，过期后移除——
        权威记录已落审计表，内存只留活跃工作集。
        """
        async with self._lock:
            expired = [
                rid for rid, s in self._sessions.items()
                if s.is_expired() and s.status == "pending"
            ]
            resolved: List[ApprovalSession] = []
            for rid in expired:
                session = self._sessions.pop(rid)
                session.resolve(ApprovalDecision.EXPIRED, "system", "timeout")
                event = self._events.get(rid)
                if event is not None:
                    event.set()
                resolved.append(session)
            # 已决策会话保留 10 分钟后移除（防 _sessions 无界增长）
            settled_cutoff = time.time() - 600
            settled = [
                rid for rid, s in self._sessions.items()
                if s.status != "pending"
                and (s.decided_at or 0) < settled_cutoff
            ]
            for rid in settled:
                self._sessions.pop(rid, None)
                self._events.pop(rid, None)
        for session in resolved:
            audit.record_decision_bg(
                tool_name=session.request.tool_name,
                outcome=ApprovalDecision.EXPIRED.value,
                decided_by="system",
                reason="timeout",
                channel_id=session.request.requester_channel,
                chat_id=session.request.requester_chat_id,
                user_id=session.request.requester_user_id,
                risk_level=session.request.risk_level.value,
                matched_rule=session.request.matched_rule,
            )
        if expired:
            log(f"批准会话清理: {len(expired)} 个过期会话已标记", "DEBUG", tag="批准")
        return len(expired) + len(settled)

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
                pass  # 取消属正常关闭流程（正常控制流，非异常）
        self._cleanup_task = None

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    async def get_stats(self) -> Dict[str, Any]:
        """获取统计信息：挂起数（内存实时）+ 决策聚合（审计账本）。"""
        async with self._lock:
            pending = sum(1 for s in self._sessions.values() if s.is_pending())
        audit_stats = await audit.stats()
        return {
            "pending_count": pending,
            "history_size": audit_stats["total"],
            "history_by_decision": audit_stats["by_outcome"],
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
