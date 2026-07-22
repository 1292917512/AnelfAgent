"""统一权限规则引擎 — Anelf 全部工具权限的单一求值点。

设计目标（合并原 ApprovalPolicy/白名单/频道规则三套机制）：
- 单一规则模型：``工具名(参数glob)`` + effect(allow/ask/deny) + scope(global/频道)
- 单一求值管线：频道deny → 全局deny → 频道ask → 全局ask → 频道allow → 全局allow → 默认
- 每个决策都带 Verdict（决策 + 命中规则 + 原因），拒绝原因全链路可见
- 旧 ``approval_policies.json`` 自动转换加载，平滑迁移

存储：``config/permission_rules.json``（热重载由 config_watcher 负责）。
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import tempfile
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.log import log

from .policy import ApprovalPolicySet, RiskLevel, match_path_pattern, matchable_arg_candidates

RULES_PATH = "config/permission_rules.json"
LEGACY_PATH = "config/approval_policies.json"

# 命令执行类工具：参数 glob 的比对对象是命令字符串
COMMAND_TOOLS = frozenset({"run_shell_command", "python_exec"})
# 复合命令特征（与 gate 创建永久规则时的拦截共用同一正则）
COMPOUND_CMD_RE = re.compile(r"&&|\|\||[;|\n\r]|`\s*[^`]|\$\(")


class PermissionEffect(str, Enum):
    """规则效果。"""

    ALLOW = "allow"    # 直接放行
    ASK = "ask"        # 请求人工批准
    DENY = "deny"      # 直接拒绝


class PermissionDecision(str, Enum):
    """求值结论。"""

    AUTO_ALLOW = "auto_allow"
    ASK = "ask"
    AUTO_DENY = "auto_deny"


class PermissionRule(BaseModel):
    """单条权限规则。

    pattern 形式：
    - ``run_shell_command`` — 精确工具名
    - ``web_*`` — 工具名 glob
    - ``run_shell_command(npm test*)`` — 工具名(关键参数 glob)
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    pattern: str = Field(..., description="工具名 glob 或 工具名(参数glob)")
    effect: PermissionEffect = Field(..., description="allow / ask / deny")
    scope: str = Field(default="global", description="global 或频道 id（如 telegram、webui）")
    users: List[str] = Field(default_factory=list, description="限定用户 ID（空=所有用户）")
    risk_level: RiskLevel = Field(default=RiskLevel.MEDIUM)
    timeout_seconds: float = Field(default=60.0, description="ask 规则的超时时间")
    on_timeout: str = Field(default="deny", description="超时默认动作 deny/allow/halt")
    trust_after_n_approvals: int = Field(default=0, description="批准 N 次后自动信任（0=永远问）")
    description: str = Field(default="")
    enabled: bool = Field(default=True)
    created_by: str = Field(default="", description="创建来源（manual/web_approve/session）")
    created_at: float = Field(default_factory=time.time)

    def _split_pattern(self) -> "tuple[str, str]":
        pattern = self.pattern.strip()
        if pattern.endswith(")") and "(" in pattern:
            name, _, arg = pattern[:-1].partition("(")
            return name.strip(), arg.strip()
        return pattern, ""

    def matches(self, tool_name: str, tool_args: Optional[Dict[str, Any]],
                channel_id: str, user_id: str) -> bool:
        """判断规则是否命中本次调用。

        参数模式匹配语义：
        - 含 ``/`` 时走路径段感知匹配（``*`` 不跨目录、``**`` 跨目录），否则 fnmatch
        - 命令类工具的 allow 规则 fail-closed：arg_pattern 含通配符且候选命令
          含复合特征（&&/;/|/换行/$()/反引号）时不命中（``npm *`` 的 ``*`` 不能
          跨复合命令边界放行），降级由后续规则或默认效果处理
        """
        if not self.enabled:
            return False
        if self.users and user_id not in self.users:
            return False
        name_pattern, arg_pattern = self._split_pattern()
        if not fnmatch.fnmatch(tool_name, name_pattern):
            return False
        if arg_pattern:
            if tool_args is None:
                return False
            # 绝对路径与 workspace 相对形式双候选（等价生效，防 ../、~ 绕过）
            candidates = matchable_arg_candidates(tool_name, tool_args)
            if (
                self.effect == PermissionEffect.ALLOW
                and tool_name in COMMAND_TOOLS
                and any(ch in arg_pattern for ch in "*?[")
                and any(COMPOUND_CMD_RE.search(c) for c in candidates)
            ):
                return False
            if "/" in arg_pattern:
                return any(match_path_pattern(candidate, arg_pattern) for candidate in candidates)
            return any(
                fnmatch.fnmatch(candidate, arg_pattern)
                for candidate in candidates
            )
        return True

    def applies_to_channel(self, channel_id: str) -> bool:
        return self.scope == "global" or self.scope == channel_id


class PermissionVerdict(BaseModel):
    """求值结论（决策 + 命中规则 + 可读原因）。"""

    decision: PermissionDecision
    rule: Optional[PermissionRule] = None
    reason: str = ""

    @property
    def matched_pattern(self) -> str:
        return self.rule.pattern if self.rule else ""


class PermissionRuleSet(BaseModel):
    """权限规则集。"""

    rules: List[PermissionRule] = Field(default_factory=list)
    default_effect: PermissionEffect = Field(
        default=PermissionEffect.ALLOW,
        description="无规则命中时的默认效果（建议 allow，高危操作由规则显式 ask/deny）",
    )
    default_risk: RiskLevel = Field(default=RiskLevel.LOW)

    def evaluate(self, tool_name: str, tool_args: Optional[Dict[str, Any]] = None,
                 channel_id: str = "", user_id: str = "") -> PermissionVerdict:
        """求值（命中即返回，顺序即优先级）：

        1. 用户限定 deny（黑名单最优先，安全方向）
        2. 频道 deny → 全局 deny（显式拒绝优先于白名单）
        3. 用户限定 allow（白名单免审批，兼容旧 auto_approve_users 语义）
        4. 频道 ask → 全局 ask
        5. 频道 allow → 全局 allow
        6. 默认效果
        """
        applicable = [
            r for r in self.rules
            if r.applies_to_channel(channel_id) and r.matches(tool_name, tool_args, channel_id, user_id)
        ]

        def _pick(effect: PermissionEffect, *, user_scoped: bool = False,
                  channel_first: bool = False) -> Optional[PermissionRule]:
            candidates = [r for r in applicable if r.effect == effect]
            if user_scoped:
                candidates = [r for r in candidates if r.users]
            else:
                candidates = [r for r in candidates if not r.users]
            if channel_first:
                for scope_kind in ("channel", "global"):
                    for rule in candidates:
                        if (scope_kind == "channel") == (rule.scope != "global"):
                            return rule
                return None
            return candidates[0] if candidates else None

        def _verdict(rule: PermissionRule) -> PermissionVerdict:
            decision = {
                PermissionEffect.DENY: PermissionDecision.AUTO_DENY,
                PermissionEffect.ASK: PermissionDecision.ASK,
                PermissionEffect.ALLOW: PermissionDecision.AUTO_ALLOW,
            }[rule.effect]
            reason = f"命中规则 [{rule.pattern}]（{rule.scope}）"
            if rule.users:
                reason += f"（限定用户）"
            if rule.description:
                reason += f"：{rule.description}"
            return PermissionVerdict(decision=decision, rule=rule, reason=reason)

        # 1. 用户限定 deny
        rule = _pick(PermissionEffect.DENY, user_scoped=True)
        if rule:
            return _verdict(rule)
        # 2. 频道 deny → 全局 deny
        rule = _pick(PermissionEffect.DENY, channel_first=True)
        if rule:
            return _verdict(rule)
        # 3. 用户限定 allow
        rule = _pick(PermissionEffect.ALLOW, user_scoped=True)
        if rule:
            return _verdict(rule)
        # 4. 频道 ask → 全局 ask
        rule = _pick(PermissionEffect.ASK, channel_first=True)
        if rule:
            return _verdict(rule)
        # 5. 频道 allow → 全局 allow
        rule = _pick(PermissionEffect.ALLOW, channel_first=True)
        if rule:
            return _verdict(rule)

        if self.default_effect == PermissionEffect.DENY:
            return PermissionVerdict(
                decision=PermissionDecision.AUTO_DENY,
                reason="未命中任何规则，默认策略为拒绝",
            )
        if self.default_effect == PermissionEffect.ASK:
            return PermissionVerdict(
                decision=PermissionDecision.ASK,
                rule=PermissionRule(pattern="*", effect=PermissionEffect.ASK,
                                    risk_level=self.default_risk),
                reason="未命中任何规则，默认请求批准",
            )
        return PermissionVerdict(decision=PermissionDecision.AUTO_ALLOW, reason="无需批准")

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def to_file_dict(self) -> Dict[str, Any]:
        return {
            "default_effect": self.default_effect.value,
            "rules": [json.loads(r.model_dump_json()) for r in self.rules],
        }

    @classmethod
    def from_file_dict(cls, data: Dict[str, Any]) -> "PermissionRuleSet":
        rules = [PermissionRule(**r) for r in data.get("rules", [])]
        default_effect = PermissionEffect(data.get("default_effect", "allow"))
        return cls(rules=rules, default_effect=default_effect)


# 上一次成功加载的规则集（解析失败时保留，避免损坏文件导致规则被清空）
_last_good_rules: Optional["PermissionRuleSet"] = None


def load_rules(path: str = RULES_PATH) -> PermissionRuleSet:
    """加载规则集（自动识别新旧格式）；文件不存在时尝试转换旧 approval_policies.json。

    解析失败时 fail-closed：保留内存中上一次成功加载的规则集；若从未成功
    加载过，返回 default_effect=ASK 的集合（全部询问），避免损坏的规则文件
    导致权限被静默放开。
    """
    global _last_good_rules
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if "rules" in data:
                rule_set = PermissionRuleSet.from_file_dict(data)
                _last_good_rules = rule_set
                return rule_set
            if "policies" in data:
                # 旧格式：ApprovalPolicySet 自动转换
                policy_set = ApprovalPolicySet.load_from_file(path)
                converted = from_legacy_policyset(policy_set)
                log(f"已从旧审批策略转换 {len(converted.rules)} 条权限规则", tag="权限")
                _last_good_rules = converted
                return converted
            log(f"权限规则文件格式未知: {path}，使用空规则集", "WARNING", tag="权限")
            return PermissionRuleSet()
        except Exception as exc:
            if _last_good_rules is not None:
                log(f"权限规则文件损坏，解析失败: {exc}；已保留上一次成功加载的规则集",
                    "WARNING", tag="权限")
                return _last_good_rules
            log(f"权限规则文件损坏，解析失败: {exc}；已降级为全部询问",
                "WARNING", tag="权限")
            return PermissionRuleSet(default_effect=PermissionEffect.ASK)
    legacy = load_legacy_rules()
    if legacy is not None:
        log(f"已从旧审批策略转换 {len(legacy.rules)} 条权限规则", tag="权限")
        return legacy
    return default_rules()


def default_rules() -> PermissionRuleSet:
    """默认规则集（无配置文件时）：仅自发 Plan 确认默认询问。"""
    return PermissionRuleSet(rules=[
        PermissionRule(
            pattern="present_plan",
            effect=PermissionEffect.ASK,
            risk_level=RiskLevel.MEDIUM,
            timeout_seconds=300.0,
            description="Agent 自发提交的执行计划，默认需用户确认",
            created_by="builtin",
        ),
    ])


def load_legacy_rules(path: str = LEGACY_PATH) -> Optional[PermissionRuleSet]:
    """把旧 ApprovalPolicySet 转换为统一规则集。"""
    if not os.path.exists(path):
        return None
    policy_set = ApprovalPolicySet.load_from_file(path)
    return from_legacy_policyset(policy_set)


def from_legacy_policyset(policy_set: ApprovalPolicySet) -> PermissionRuleSet:
    """旧策略 → 新规则映射：

    - requires_approval=True → ask 规则
    - requires_approval=False（非 "*"）→ allow 规则
    - auto_approve_users → 附加 allow 规则（限定用户，优先级靠 users 过滤）
    - auto_deny_users → 附加 deny 规则（限定用户）
    - "*" 兜底 → default_effect
    """
    rules: List[PermissionRule] = []
    default_effect = PermissionEffect.ALLOW
    for p in policy_set.policies:
        base: Dict[str, Any] = {
            "pattern": p.tool_name_pattern,
            "risk_level": p.risk_level,
            "timeout_seconds": p.timeout_seconds,
            "on_timeout": p.on_timeout,
            "trust_after_n_approvals": p.trust_after_n_approvals,
            "description": p.description,
            "created_by": "legacy_migration",
        }
        if p.tool_name_pattern == "*" and not p.requires_approval:
            default_effect = PermissionEffect.ALLOW
            continue
        rules.append(PermissionRule(
            **base,
            effect=PermissionEffect.ASK if p.requires_approval else PermissionEffect.ALLOW,
        ))
        for uid in p.auto_approve_users:
            rules.append(PermissionRule(**base, effect=PermissionEffect.ALLOW, users=[uid]))
        for uid in p.auto_deny_users:
            rules.append(PermissionRule(**base, effect=PermissionEffect.DENY, users=[uid]))
    return PermissionRuleSet(rules=rules, default_effect=default_effect)


def save_rules(rule_set: PermissionRuleSet, path: str = RULES_PATH) -> None:
    """保存规则集到文件（tmp 文件 + os.replace 原子写，避免中断产生截断文件）。"""
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".permission_rules.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rule_set.to_file_dict(), f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
