"""批准门 — 工具调用前的人工确认入口。

职责：
1. 匹配策略（policy_set.match(tool_name)）
2. 检查是否自动批准（白名单 / 信任阈值）
3. 创建批准会话
4. 通过频道发送批准提示
5. 等待用户决策（同步阻塞或超时）
6. 返回决策结果

使用方式：
    gate = get_approval_gate()
    decision = await gate.request_approval(
        tool_name="filesystem.write_file",
        tool_args={"path": "/tmp/x", "content": "..."},
        reason="high risk write",
        channel=current_channel,
        chat_id="...",
        user_id="...",
    )
    if decision != ApprovalDecision.APPROVED:
        raise ApprovalDenied(...)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from core.log import log

from agent.channel.base import ApprovalPromptRenderContext, BaseChannel

from .manager import ApprovalManager, get_approval_manager
from .policy import ApprovalPolicy, ApprovalPolicySet, RiskLevel
from .session import ApprovalDecision, ApprovalRequest, ApprovalSession


class ApprovalDenied(Exception):
    """批准被拒绝。"""

    def __init__(self, decision: ApprovalDecision, reason: str = "") -> None:
        self.decision = decision
        self.reason = reason
        super().__init__(f"Approval {decision.value}: {reason}")


class ApprovalGate:
    """批准门（单例）。"""

    def __init__(
        self,
        manager: Optional[ApprovalManager] = None,
        policy_set: Optional[ApprovalPolicySet] = None,
    ) -> None:
        self._manager = manager or get_approval_manager()
        self._policy_set = policy_set or ApprovalPolicySet.default()

    # ------------------------------------------------------------------
    # 策略管理
    # ------------------------------------------------------------------

    def set_policy_set(self, policy_set: ApprovalPolicySet) -> None:
        """替换策略集。"""
        self._policy_set = policy_set

    def get_policy_set(self) -> ApprovalPolicySet:
        """获取当前策略集。"""
        return self._policy_set

    def reload_policies(self, path: str) -> None:
        """从文件重载策略。"""
        self._policy_set = ApprovalPolicySet.load_from_file(path)
        log(f"批准策略已重载: {path} ({len(self._policy_set.policies)} 条)", tag="批准")

    # ------------------------------------------------------------------
    # 批准请求
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        *,
        tool_name: str,
        tool_args: Dict[str, Any],
        reason: str,
        channel: BaseChannel,
        chat_id: str,
        user_id: str,
        timeout: Optional[float] = None,
    ) -> ApprovalDecision:
        """请求批准（核心入口）。

        Args:
            tool_name: 工具名
            tool_args: 工具参数（会被脱敏后展示）
            reason: 触发批准的原因
            channel: 来源频道（用于发送批准提示）
            chat_id: 会话 ID
            user_id: 发起用户 ID
            timeout: 超时时间（秒），None 则使用策略默认

        Returns:
            ApprovalDecision.APPROVED / DENIED / EXPIRED / CANCELLED

        Raises:
            ApprovalDenied: 被拒绝或超时（如果调用方希望以异常方式处理）
        """
        # 1. 匹配策略
        policy = self._policy_set.match(tool_name)
        if not policy or not policy.requires_approval:
            return ApprovalDecision.APPROVED  # 无需批准

        # 2. 检查自动决策
        if policy.is_auto_approved(user_id):
            log(f"用户 {user_id} 在白名单，自动批准: {tool_name}", "DEBUG", tag="批准")
            return ApprovalDecision.APPROVED
        if policy.is_auto_denied(user_id):
            log(f"用户 {user_id} 在黑名单，自动拒绝: {tool_name}", "DEBUG", tag="批准")
            return ApprovalDecision.DENIED
        if await self._manager.is_trusted(tool_name, user_id, policy):
            log(f"用户 {user_id} 已信任 {tool_name}，自动批准", "DEBUG", tag="批准")
            return ApprovalDecision.APPROVED

        # 3. 创建请求
        timeout_seconds = timeout or policy.timeout_seconds
        request = ApprovalRequest(
            tool_name=tool_name,
            tool_args=self._sanitize_args(tool_args),
            risk_level=policy.risk_level,
            reason=reason,
            requester_channel=channel.channel_id,
            requester_chat_id=chat_id,
            requester_user_id=user_id,
            expires_at=time.time() + timeout_seconds,
        )
        session = await self._manager.create_session(request)

        # 4. 发送批准提示
        try:
            await self._send_approval_prompt(channel, chat_id, session)
        except Exception as exc:
            log(f"发送批准提示失败: {exc}", "ERROR", tag="批准")
            await self._manager.cancel(request.request_id, "send_prompt_failed")
            return ApprovalDecision.CANCELLED

        # 5. 等待决策
        decision = await self._wait_for_decision(session, timeout_seconds)

        # 6. 根据策略处理结果
        if decision == ApprovalDecision.EXPIRED:
            if policy.on_timeout == "allow":
                log(f"批准超时但策略允许: {tool_name}", "WARNING", tag="批准")
                return ApprovalDecision.APPROVED
            elif policy.on_timeout == "halt":
                raise ApprovalDenied(ApprovalDecision.EXPIRED, "timeout halt")
            # 默认 deny
            return ApprovalDecision.DENIED

        return decision

    async def approve(self, request_id: str, decided_by: str = "", reason: str = "") -> bool:
        """批准（供外部调用，如命令处理器）。"""
        return await self._manager.approve(request_id, decided_by, reason)

    async def deny(self, request_id: str, decided_by: str = "", reason: str = "") -> bool:
        """拒绝。"""
        return await self._manager.deny(request_id, decided_by, reason)

    async def cancel(self, request_id: str, reason: str = "") -> bool:
        """取消。"""
        return await self._manager.cancel(request_id, reason)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _sanitize_args(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """脱敏工具参数（移除 API Key / Token / 密码等）。"""
        sensitive_keys = {"api_key", "token", "password", "secret", "key", "auth"}
        sanitized: Dict[str, Any] = {}
        for k, v in args.items():
            if any(s in k.lower() for s in sensitive_keys):
                sanitized[k] = "***REDACTED***"
            elif isinstance(v, str) and len(v) > 200:
                sanitized[k] = v[:200] + "..."
            else:
                sanitized[k] = v
        return sanitized

    async def _send_approval_prompt(
        self,
        channel: BaseChannel,
        chat_id: str,
        session: ApprovalSession,
    ) -> None:
        """发送批准提示到频道。"""
        ctx = ApprovalPromptRenderContext(
            request_id=session.request.request_id,
            tool_name=session.request.tool_name,
            tool_args_summary=str(session.request.tool_args),
            risk_level=session.request.risk_level.value,
            reason=session.request.reason,
            timeout_seconds=session.request.expires_at - time.time(),
        )
        request = await channel.render_approval_prompt(ctx)
        # 填充 chat_id（render_approval_prompt 返回的 SendRequest 可能 chat_id 为空）
        request.channel.channel_id = chat_id
        response = await channel.forward_message(request)
        if not response.success:
            raise RuntimeError(f"批准提示发送失败: {response.error}")

    async def _wait_for_decision(
        self,
        session: ApprovalSession,
        timeout: float,
    ) -> ApprovalDecision:
        """等待决策（轮询）。"""
        deadline = time.time() + timeout
        poll_interval = 0.5
        while time.time() < deadline:
            current = await self._manager.get_session(session.request.request_id)
            if not current or current.status != "pending":
                break
            if current.decision:
                return current.decision
            await asyncio.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.2, 2.0)  # 渐进退避

        # 超时
        await self._manager.resolve(
            session.request.request_id,
            ApprovalDecision.EXPIRED,
            "system",
            "timeout",
        )
        return ApprovalDecision.EXPIRED


# ======================================================================
# 全局单例
# ======================================================================

_gate: Optional[ApprovalGate] = None


def get_approval_gate() -> ApprovalGate:
    """获取全局批准门。"""
    global _gate
    if _gate is None:
        _gate = ApprovalGate()
    return _gate
