"""批准策略 — 声明哪些工具需要批准、风险等级、超时。

策略来源：
1. **声明式配置**（config/approval_policies.json）：运维侧覆盖
2. **工具 metadata**（@tool(risk="high")）：开发侧默认值
3. **运行时动态**（guardrails 升级）：异常情况下临时提升

匹配优先级：声明式 > 工具 metadata > 运行时
"""

from __future__ import annotations

import fnmatch
import json
import os
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.log import log


class RiskLevel(str, Enum):
    """风险等级。"""

    LOW = "low"          # 只读查询（list_directory, get_weather）
    MEDIUM = "medium"    # 写操作但可逆（create_note, send_message）
    HIGH = "high"        # 写操作难逆（write_file, delete_file）
    CRITICAL = "critical"  # 高危（shell.exec, system.reboot, http.request to unknown host）


class ApprovalPolicy(BaseModel):
    """单个批准策略。

    匹配规则：tool_name_pattern 支持 glob（如 "filesystem.*"），
    也支持 Claude Code 风格的参数模式 ``工具名(参数glob)``，
    如 ``run_shell_command(npm test*)``、``edit_file(config/**)`` —
    此时仅当工具的关键参数（命令字符串/文件路径）同时匹配才命中。
    """

    tool_name_pattern: str = Field(..., description="工具名 glob 模式，可带 (参数glob)")
    risk_level: RiskLevel = Field(..., description="风险等级")
    requires_approval: bool = Field(default=True, description="是否需要批准")
    timeout_seconds: float = Field(default=60.0, description="超时时间")
    auto_approve_users: List[str] = Field(default_factory=list, description="白名单用户")
    auto_deny_users: List[str] = Field(default_factory=list, description="黑名单用户")
    on_timeout: str = Field(default="deny", description="超时默认动作 (deny/allow/halt)")
    trust_after_n_approvals: int = Field(default=0, description="批准 N 次后自动信任（0=永远问）")
    description: str = Field(default="", description="策略描述")

    def _split_pattern(self) -> "tuple[str, str]":
        """拆分 ``工具名(参数模式)`` 为 (工具名模式, 参数模式)。"""
        pattern = self.tool_name_pattern.strip()
        if pattern.endswith(")") and "(" in pattern:
            name, _, arg = pattern[:-1].partition("(")
            return name.strip(), arg.strip()
        return pattern, ""

    def matches(self, tool_name: str, tool_args: Optional[Dict[str, Any]] = None) -> bool:
        """检查工具（及参数）是否匹配本策略。"""
        name_pattern, arg_pattern = self._split_pattern()
        if not fnmatch.fnmatch(tool_name, name_pattern):
            return False
        if not arg_pattern:
            return True
        if tool_args is None:
            # 无参数上下文时参数模式不命中（fail-closed，不误放行）
            return False
        return fnmatch.fnmatch(extract_matchable_arg(tool_name, tool_args), arg_pattern)

    def is_auto_approved(self, user_id: str) -> bool:
        """检查用户是否在白名单中。"""
        return user_id in self.auto_approve_users

    def is_auto_denied(self, user_id: str) -> bool:
        """检查用户是否在黑名单中。"""
        return user_id in self.auto_deny_users


# 各工具的"关键参数"提取器：参数模式匹配的比对对象
_ARG_KEYS: Dict[str, tuple] = {
    "run_shell_command": ("command",),
    "python_exec": ("code",),
    "read_file": ("path",),
    "write_file": ("path",),
    "edit_file": ("file_path",),
    "append_file": ("path",),
    "delete_file": ("path",),
    "move_file": ("src", "dst"),
    "copy_file": ("src", "dst"),
    "mkdir": ("path",),
    "web_fetch": ("url",),
    "web_request": ("url",),
}


def extract_matchable_arg(tool_name: str, tool_args: Dict[str, Any]) -> str:
    """提取工具的关键参数用于 ``工具名(参数glob)`` 匹配。

    已知工具取其关键参数（命令/路径/URL），多值以空格连接；
    未知工具退化为参数的紧凑 JSON。
    """
    keys = _ARG_KEYS.get(tool_name)
    if keys:
        return " ".join(str(tool_args.get(k, "")) for k in keys).strip()
    return json.dumps(tool_args, ensure_ascii=False, sort_keys=True)


class ApprovalPolicySet(BaseModel):
    """批准策略集合。"""

    policies: List[ApprovalPolicy] = Field(default_factory=list)
    default_policy: Optional[ApprovalPolicy] = Field(
        default=None,
        description="默认策略（不匹配任何策略时使用）",
    )

    def match(self, tool_name: str,
              tool_args: Optional[Dict[str, Any]] = None) -> Optional[ApprovalPolicy]:
        """匹配第一个符合的策略。优先精确匹配，再参数模式，再 glob。"""
        # 精确匹配优先
        for p in self.policies:
            if p.tool_name_pattern == tool_name:
                return p
        # 参数模式匹配（工具名(参数glob)）
        for p in self.policies:
            if "(" in p.tool_name_pattern and p.matches(tool_name, tool_args):
                return p
        # glob 匹配（带参数模式的策略也需要参与，参数不匹配则跳过）
        for p in self.policies:
            if p.matches(tool_name, tool_args):
                return p
        return self.default_policy

    @classmethod
    def load_from_file(cls, path: str) -> "ApprovalPolicySet":
        """从 JSON 文件加载策略集。"""
        if not os.path.exists(path):
            log(f"批准策略文件不存在: {path}，使用空策略集", "WARNING", tag="批准")
            return cls(policies=[])
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            policies = [ApprovalPolicy(**p) for p in data.get("policies", [])]
            default_data = data.get("default_policy")
            default = ApprovalPolicy(**default_data) if default_data else None
            return cls(policies=policies, default_policy=default)
        except Exception as exc:
            log(f"批准策略文件解析失败: {exc}", "ERROR", tag="批准")
            return cls(policies=[])

    @classmethod
    def default(cls) -> "ApprovalPolicySet":
        """返回默认策略集（仅 CRITICAL 需要批准）。"""
        return cls(
            policies=[
                ApprovalPolicy(
                    tool_name_pattern="shell.*",
                    risk_level=RiskLevel.CRITICAL,
                    requires_approval=True,
                    description="Shell 命令执行",
                ),
                ApprovalPolicy(
                    tool_name_pattern="system.*",
                    risk_level=RiskLevel.CRITICAL,
                    requires_approval=True,
                    description="系统级操作",
                ),
                ApprovalPolicy(
                    tool_name_pattern="filesystem.write*",
                    risk_level=RiskLevel.HIGH,
                    requires_approval=True,
                    description="文件写入",
                ),
                ApprovalPolicy(
                    tool_name_pattern="filesystem.delete*",
                    risk_level=RiskLevel.HIGH,
                    requires_approval=True,
                    description="文件删除",
                ),
                ApprovalPolicy(
                    tool_name_pattern="*",
                    risk_level=RiskLevel.LOW,
                    requires_approval=False,
                    description="默认低风险",
                ),
            ],
        )
