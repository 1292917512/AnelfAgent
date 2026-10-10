"""统一权限规则引擎 — Anelf 全部工具权限的单一求值点。

权限规则：
- 单一规则模型：``工具名(参数glob)`` + effect(allow/ask/deny) + scope(global/频道)
- 单一求值管线：频道deny → 全局deny → 频道ask → 全局ask → 频道allow → 全局allow
  → 工具元数据 CRITICAL 兜底 → 默认
- 每个决策都带 Verdict（决策 + 命中规则 + 原因），拒绝原因全链路可见

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

from pydantic import BaseModel, Field, field_validator, model_validator

from core.path import ConfigPaths

from .matching import matchable_arg_candidates, matches_arg_pattern
from .types import RiskLevel


def rules_path() -> str:
    """权限规则文件路径（运行时解析：ConfigPaths 动态跟随目录配置，
    def 默认参数在定义时冻结会让测试隔离写穿真实配置）。"""
    return ConfigPaths.PERMISSION_RULES


# 命令执行类工具：参数 glob 的比对对象是命令字符串
COMMAND_TOOLS = frozenset({"run_shell_command", "python_exec"})
# 通配放行不能跨复合命令边界
COMPOUND_CMD_RE = re.compile(r"&&|\|\||[;|\n\r]|`\s*[^`]|\$\(")


class PermissionEffect(str, Enum):
    """规则效果。"""

    ALLOW = "allow"    # 直接放行
    ASK = "ask"        # 交由 AI 评审
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
    pattern: str = Field(..., min_length=1, description="工具名 glob 或 工具名(参数glob)")
    effect: PermissionEffect = Field(..., description="allow / ask / deny")
    scope: str = Field(default="global", description="global 或频道 id（如 telegram、webui）")
    users: List[str] = Field(default_factory=list, description="限定用户 ID（空=所有用户）")
    risk_level: RiskLevel = Field(default=RiskLevel.MEDIUM)
    description: str = Field(default="")
    enabled: bool = Field(default=True)
    created_by: str = Field(default="", description="创建来源")
    created_at: float = Field(default_factory=time.time)

    @field_validator("pattern", "scope")
    @classmethod
    def nonempty(cls, value: str) -> str:
        """拒绝空模式和空作用域。"""
        value = value.strip()
        if not value:
            raise ValueError("pattern 和 scope 不能为空")
        return value

    @model_validator(mode="after")
    def scoped_users(self) -> "PermissionRule":
        """用户 ID 必须限定频道，避免跨平台同号身份获得权限。"""
        if self.users and self.scope == "global":
            raise ValueError("限定用户的规则必须指定频道 scope")
        self.users = list(dict.fromkeys(uid.strip() for uid in self.users if uid.strip()))
        return self

    def _split_pattern(self) -> "tuple[str, str]":
        pattern = self.pattern.strip()
        if pattern.endswith(")") and "(" in pattern:
            name, _, arg = pattern[:-1].partition("(")
            return name.strip(), arg.strip()
        return pattern, ""

    def matches(self, tool_name: str, tool_args: Optional[Dict[str, Any]] = None,
                channel_id: str = "", user_id: str = "") -> bool:
        """判断规则是否命中本次调用。

        参数模式匹配语义：
        - 含 ``/`` 时走路径段感知匹配（``*`` 不跨目录、``**`` 跨目录），否则 fnmatch
        - 命令类工具的 allow 规则 fail-closed：arg_pattern 含通配符且候选命令
          含复合特征（&&/;/|/换行/$()/反引号）时不命中（``npm *`` 的 ``*`` 不能
          跨复合命令边界放行），降级由后续规则或默认效果处理
        """
        if not self.enabled:
            return False
        if self.users:
            if user_id and user_id not in self.users:
                return False
            if not user_id and self.effect == PermissionEffect.ALLOW:
                return False
        name_pattern, arg_pattern = self._split_pattern()
        if not fnmatch.fnmatchcase(tool_name, name_pattern):
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
            return matches_arg_pattern(tool_name, tool_args, arg_pattern,
                                       require_all=self.effect == PermissionEffect.ALLOW)
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


def tool_meta_risk_rule(tool_name: str) -> Optional[PermissionRule]:
    """工具元数据声明的 CRITICAL 风险 → 合成 ask 规则（求值管线第 6 层兜底）。

    优先级：声明式规则> 工具 metadata > 默认效果。仅 CRITICAL
    升级为 ask——write_file 等高频工具即便声明 HIGH 也不能逐次评审（评审
    延迟会拖垮日常自治），MEDIUM/HIGH 只作为 risk_level 标注供 guardian
    评审与审计参考。显式 allow/ask/deny 规则命中时本层不参与（求值顺序
    天然保证）。注册表不可用或工具未注册时返回 None（维持默认效果）。
    """
    try:
        from core.entity import EntityRegistry
        entity = EntityRegistry.get(tool_name)
    except Exception:
        return None
    if entity is None:
        return None
    risk = str(entity.meta.get("risk", "") or "").strip().lower()
    if risk != RiskLevel.CRITICAL.value:
        return None
    return PermissionRule(
        pattern=_META_RISK_PATTERN,
        effect=PermissionEffect.ASK,
        risk_level=RiskLevel.CRITICAL,
        description=f"工具 {tool_name} 元数据声明 risk=CRITICAL（无显式规则覆盖时兜底）",
        created_by="tool_meta",
    )


# 元数据层合成规则的 pattern 标识（审计/展示用；不参与真实匹配）
_META_RISK_PATTERN = "meta:risk"


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
        3. 用户限定 allow（限定用户的显式放行）
        4. 频道 ask → 全局 ask
        5. 频道 allow → 全局 allow
        6. 工具元数据 CRITICAL 兜底（默认放行时，见 tool_meta_risk_rule）
        7. 默认效果
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
                candidates = [r for r in candidates if not r.users or effect == PermissionEffect.ASK]
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
                reason += "（限定用户）"
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
                reason="未命中任何规则，默认交由 AI 评审",
            )
        # 6. 工具元数据 CRITICAL 兜底：默认放行前，声明 risk=CRITICAL 且无
        # 显式规则覆盖的工具升级为 ask（guardian 先行评审，不转交人工）
        meta_rule = tool_meta_risk_rule(tool_name)
        if meta_rule is not None:
            return PermissionVerdict(
                decision=PermissionDecision.ASK,
                rule=meta_rule,
                reason=f"工具 {tool_name} 声明 CRITICAL 风险且无显式规则覆盖，交由 AI 评审",
            )
        return PermissionVerdict(decision=PermissionDecision.AUTO_ALLOW, reason="规则允许执行")

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def to_file_dict(self) -> Dict[str, Any]:
        return {
            "default_effect": self.default_effect.value,
            "default_risk": self.default_risk.value,
            "rules": [json.loads(r.model_dump_json()) for r in self.rules],
        }

    @classmethod
    def from_file_dict(cls, data: Dict[str, Any]) -> "PermissionRuleSet":
        if not isinstance(data, dict) or "rules" not in data:
            raise ValueError("权限配置必须包含 rules 数组")
        return cls.model_validate(data)


def load_rules(path: Optional[str] = None) -> PermissionRuleSet:
    """读取唯一规则文件；格式错误交由调用者保留当前规则。"""
    resolved = path or rules_path()
    try:
        with open(resolved, encoding="utf-8") as file:
            return PermissionRuleSet.from_file_dict(json.load(file))
    except FileNotFoundError:
        retired = os.path.join(os.path.dirname(resolved), "approval_policies.json")
        if os.path.exists(retired):
            raise ValueError("请在权限页面保存 permission_rules.json；approval_policies.json 已停用") from None
        return PermissionRuleSet()


def save_rules(rule_set: PermissionRuleSet, path: Optional[str] = None) -> None:
    """保存规则集到文件（tmp 文件 + os.replace 原子写，避免中断产生截断文件）。"""
    resolved = path or rules_path()
    dir_name = os.path.dirname(resolved) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".permission_rules.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(rule_set.to_file_dict(), f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, resolved)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
