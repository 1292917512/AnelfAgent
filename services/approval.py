"""Web 权限管理门面：规则配置和持久审计。"""

from typing import Any

from agent.approval import audit, get_approval_gate
from agent.approval.rules import PermissionEffect, PermissionRule, PermissionRuleSet


class ApprovalService:
    """提供权限配置和审计读写，不接受用户对运行中工具作出批准决定。"""

    @staticmethod
    async def list_history(limit: int, offset: int, tool_name: str) -> list[dict[str, Any]]:
        """按时间倒序读取裁决账本。"""
        return await audit.list_history(limit, offset, tool_name)

    @staticmethod
    async def get_stats() -> dict[str, Any]:
        """按裁决结果统计账本。"""
        return await audit.stats()

    @staticmethod
    def get_rules() -> dict[str, Any]:
        """读取唯一的持久规则集。"""
        gate = get_approval_gate()
        return {**gate.get_rule_set().to_file_dict(), "load_error": gate.load_error}

    @staticmethod
    def save_rule_set(rules: list[dict[str, Any]], default_effect: str) -> int:
        """校验并原子保存规则，成功后即时生效。"""
        gate = get_approval_gate()
        rule_set = PermissionRuleSet(
            rules=[PermissionRule.model_validate(rule) for rule in rules],
            default_effect=PermissionEffect(default_effect), default_risk=gate.get_rule_set().default_risk,
        )
        gate.set_rule_set(rule_set, persist=True)
        return len(rule_set.rules)
