"""工具权限检查的风险等级与执行结论。"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class RiskLevel(str, Enum):
    """工具声明的风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


PermissionOutcome = Literal[
    "rule_allow", "denied", "guardian_approved", "guardian_denied",
    "guardian_bypass", "permission_error",
]


@dataclass(frozen=True)
class ApprovalResult:
    """规则与 AI 评审结论；notice 仅供执行任务的 AI 复核。"""

    allowed: bool
    outcome: PermissionOutcome
    reason: str = ""
    notice: str = ""
