"""审批机制服务 -- 待批准请求、审计历史、策略集与统一权限规则管理。"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List


class ApprovalService:
    """审批管理服务（Web 侧入口，封装 agent.approval 门与管理器）。"""

    # ------------------------------------------------------------------
    # 待批准请求 / 决策
    # ------------------------------------------------------------------

    @staticmethod
    async def list_pending() -> List[Dict[str, Any]]:
        """列出所有待批准的请求（序列化为前端结构）。"""
        from agent.approval import get_approval_manager
        manager = get_approval_manager()
        pending = await manager.list_pending()
        return [
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
                "matched_rule": s.request.matched_rule,
            }
            for s in pending
        ]

    @staticmethod
    async def list_history(limit: int, offset: int, tool_name: str) -> List[Dict[str, Any]]:
        """列出历史决策记录（审计账本，按时间倒序分页）。"""
        from agent.approval import audit
        rows = await audit.list_history(limit, offset, tool_name)
        return [
            {
                "id": r["id"],
                "ts_ns": r["ts_ns"],
                "tool_name": r["tool_name"],
                "outcome": r["outcome"],
                "decided_by": r["decided_by"],
                "reason": r["reason"],
                "channel_id": r["channel_id"],
                "chat_id": r["chat_id"],
                "user_id": r["user_id"],
                "risk_level": r["risk_level"],
                "matched_rule": r["matched_rule"],
                "args_json": r["args_json"],
            }
            for r in rows
        ]

    @staticmethod
    async def approve(request_id: str, *, reason: str, remember: str) -> bool:
        """批准请求（remember: once / session / always）。"""
        from agent.approval import get_approval_gate
        gate = get_approval_gate()
        return await gate.approve(request_id, decided_by="webui", reason=reason,
                                  remember=remember)

    @staticmethod
    async def deny(request_id: str, *, reason: str) -> bool:
        """拒绝请求。"""
        from agent.approval import get_approval_gate
        gate = get_approval_gate()
        return await gate.deny(request_id, decided_by="webui", reason=reason)

    @staticmethod
    async def get_stats() -> Dict[str, Any]:
        """获取统计信息。"""
        from agent.approval import get_approval_manager
        manager = get_approval_manager()
        return await manager.get_stats()

    # ------------------------------------------------------------------
    # 策略集（旧模型）
    # ------------------------------------------------------------------

    @staticmethod
    def get_policies() -> List[Dict[str, Any]]:
        """获取当前策略集（序列化为前端结构）。"""
        from agent.approval import get_approval_gate
        gate = get_approval_gate()
        policy_set = gate.get_policy_set()
        return [
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
        ]

    @staticmethod
    def save_policies(policies: List[Dict[str, Any]]) -> int:
        """保存策略集到审批门并落盘（触发 ConfigWatcher 自动重载）。

        Returns:
            保存的策略条数。

        Raises:
            Exception: 策略数据非法或落盘失败（路由层映射 400）。
        """
        from agent.approval import get_approval_gate
        from agent.approval.policy import ApprovalPolicy, ApprovalPolicySet

        parsed = [ApprovalPolicy(**p) for p in policies]
        policy_set = ApprovalPolicySet(policies=parsed)

        gate = get_approval_gate()
        gate.set_policy_set(policy_set)

        # 保存到文件（触发 ConfigWatcher 自动重载）
        from core.path import ConfigPaths
        policies_path = ConfigPaths.APPROVAL_POLICIES
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
                        for p in parsed
                    ],
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        return len(parsed)

    # ------------------------------------------------------------------
    # 统一权限规则（新模型：allow/ask/deny + global/频道 scope）
    # ------------------------------------------------------------------

    @staticmethod
    def get_rules() -> Dict[str, Any]:
        """获取统一权限规则集（含会话级规则标注）。"""
        from agent.approval import get_approval_gate
        gate = get_approval_gate()
        return {
            "default_effect": gate.get_rule_set().default_effect.value,
            "rules": [json.loads(r.model_dump_json()) for r in gate.get_rule_set().rules],
            "persisted_count": len(gate.get_rule_set().rules) - gate.session_rule_count(),
            "session_count": gate.session_rule_count(),
        }

    @staticmethod
    def save_rule_set(rules: List[Dict[str, Any]], default_effect: str) -> int:
        """整体保存规则集（写入 config/permission_rules.json，触发热重载）。

        Returns:
            保存的规则条数。

        Raises:
            Exception: 规则数据非法（路由层映射 400）。
        """
        from agent.approval import get_approval_gate
        from agent.approval.rules import PermissionEffect, PermissionRule, PermissionRuleSet

        rule_set = PermissionRuleSet(
            rules=[PermissionRule(**r) for r in rules],
            default_effect=PermissionEffect(default_effect),
        )
        gate = get_approval_gate()
        gate.set_rule_set(rule_set, persist=True)
        return len(rule_set.rules)

    @staticmethod
    def add_rule(
        *,
        pattern: str,
        effect: str,
        scope: str,
        users: List[str],
        risk_level: str,
        timeout_seconds: float,
        on_timeout: str,
        description: str,
    ) -> str:
        """添加单条规则（持久化），返回规则 ID。

        Raises:
            Exception: 规则数据非法（路由层映射 400）。
        """
        from agent.approval import get_approval_gate
        from agent.approval.rules import PermissionEffect, PermissionRule, RiskLevel

        rule = PermissionRule(
            pattern=pattern,
            effect=PermissionEffect(effect),
            scope=scope,
            users=users,
            risk_level=RiskLevel(risk_level),
            timeout_seconds=timeout_seconds,
            on_timeout=on_timeout,
            description=description,
            created_by="webui",
        )
        gate = get_approval_gate()
        gate.add_rule(rule, persist=True)
        return rule.id

    @staticmethod
    def delete_rule(rule_id: str) -> bool:
        """删除规则（先查持久规则，再查会话规则），返回是否删除成功。"""
        from agent.approval import get_approval_gate
        gate = get_approval_gate()
        return gate.delete_rule(rule_id)
