"""工具权限入口：规则裁决、AI 评审和审计，不依赖频道传输或人工会话。"""

from __future__ import annotations

import threading
from typing import Any

from agent.messages.everything import parse_entity_scope
from core.log import log
from core.sanitizer import sanitize_text

from . import audit
from .guardian import get_approval_guardian
from .redaction import redact_arguments
from .rules import PermissionDecision, PermissionRuleSet, load_rules, save_rules
from .types import ApprovalResult


class ApprovalGate:
    """以不可变快照读取规则，持久化成功后原子发布配置。"""

    def __init__(self, rule_set: PermissionRuleSet | None = None) -> None:
        self._lock = threading.RLock()
        self._rule_set = PermissionRuleSet()
        self._loaded = rule_set is not None
        self.load_error = ""
        if rule_set is None:
            self.reload_rules()
        else:
            self._rule_set = rule_set.model_copy(deep=True)

    def get_rule_set(self) -> PermissionRuleSet:
        """获取当前规则副本，避免外部修改运行中配置。"""
        with self._lock:
            return self._rule_set.model_copy(deep=True)

    def set_rule_set(self, rule_set: PermissionRuleSet, *, persist: bool = False) -> None:
        """替换规则集，写盘失败时保留原规则。"""
        snapshot = rule_set.model_copy(deep=True)
        with self._lock:
            if persist:
                save_rules(snapshot)
            self._rule_set = snapshot
            self._loaded = True
            self.load_error = ""

    def reload_rules(self, path: str = "") -> None:
        """热加载规则；损坏文件不覆盖当前策略，首次失败时拒绝执行。"""
        from .rules import PermissionEffect
        with self._lock:
            try:
                snapshot = load_rules(path or None)
            except Exception as exc:
                if not getattr(self, "_loaded", False):
                    self._rule_set = PermissionRuleSet(default_effect=PermissionEffect.DENY)
                self.load_error = str(exc)
                log(f"权限规则加载失败，保留当前策略: {exc}", "ERROR", tag="权限")
                return
            self._rule_set = snapshot
            self._loaded = True
            self.load_error = ""

    async def check(
        self, *, tool_name: str, tool_args: dict[str, Any], reason: str,
        scope: str = "", channel_id: str = "", chat_id: str = "", user_id: str = "",
    ) -> ApprovalResult:
        """检查调用权限；Guardian 不可用时自主执行，规则故障则拒绝。

        Model Experience:
        - 模型看到可解释的拒绝原因或评审降级提醒，不会收到人工批准指令。
        - 仅异常与高风险结论增加有界尾部文本，常规放行不增加 token。
        - 不修改 stable prompt，主对话前缀缓存保持稳定。
        """
        scope_type, adapter, identity, session = parse_entity_scope(scope)
        channel_id = channel_id or adapter
        chat_id = chat_id or session or identity or scope
        user_id = user_id or (identity if scope_type == "user" else "")
        try:
            rules = self.get_rule_set()
            verdict = rules.evaluate(tool_name, tool_args, channel_id, user_id)
        except Exception as exc:
            result = ApprovalResult(False, "permission_error", f"权限规则求值失败: {type(exc).__name__}")
            await audit.record_decision(
                tool_name=tool_name, outcome=result.outcome, decided_by="system",
                reason=result.reason, channel_id=channel_id, chat_id=chat_id, user_id=user_id,
            )
            return result
        if verdict.decision == PermissionDecision.AUTO_ALLOW:
            return ApprovalResult(True, "rule_allow", verdict.reason)

        risk = verdict.rule.risk_level if verdict.rule else rules.default_risk
        arguments = redact_arguments(tool_args)
        if verdict.decision == PermissionDecision.AUTO_DENY:
            result = ApprovalResult(False, "denied", verdict.reason)
            decided_by = "rule"
        else:
            review = await get_approval_guardian().review(
                tool_name=tool_name, tool_args=arguments, reason=sanitize_text(reason),
                risk_level=risk.value, channel_id=channel_id, user_id=user_id,
            )
            rationale = sanitize_text(review.rationale)[:500]
            if review.approved is None:
                result = ApprovalResult(
                    True, "guardian_bypass", rationale,
                    f"{tool_name} 未完成 AI 安全评审（{rationale}），按自主策略执行。请复核操作范围和影响。",
                )
                decided_by = "system"
            elif review.approved:
                notice = (f"{tool_name} 已通过 AI 评审，但存在高风险：{rationale}。请复核操作范围和影响。"
                          if review.risk == "high" else "")
                result = ApprovalResult(True, "guardian_approved", rationale, notice)
                decided_by = "guardian"
            else:
                result = ApprovalResult(False, "guardian_denied", rationale or "AI 评审拒绝执行")
                decided_by = "guardian"
        await audit.record_decision(
            tool_name=tool_name, outcome=result.outcome, decided_by=decided_by,
            reason=result.reason, channel_id=channel_id, chat_id=chat_id, user_id=user_id,
            risk_level=risk.value, matched_rule=verdict.matched_pattern, tool_args=arguments,
        )
        return result


_gate: ApprovalGate | None = None


def get_approval_gate() -> ApprovalGate:
    """获取进程共用的权限入口。"""
    global _gate
    if _gate is None:
        _gate = ApprovalGate()
    return _gate
