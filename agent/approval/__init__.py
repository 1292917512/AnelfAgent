"""批准机制 — 工具调用前的确认门（统一权限引擎 + Guardian 自动评审）。

- **rules.py**: 统一权限规则模型与求值引擎
  （``工具名(参数glob)`` + allow/ask/deny + global/频道 scope）
- **policy.py**: 旧 ApprovalPolicy（保留兼容，新规则引擎可自动转换）
- **session.py**: ApprovalSession 表示一次挂起的批准请求
- **guardian.py**: ask 触发时的 LLM 自动评审（含超时与失败熔断）
- **gate.py**: ApprovalGate 入口：求值、guardian 评审、批准会话、记住决策
- **renderer.py**: 各频道渲染抽象（默认文本，子类可覆盖为按钮/卡片/SSE 弹窗）
- **manager.py**: 挂起批准请求的全局管理与事件驱动等待

集成点：
- `agent/mind/tools/think_loop.py` 在 execute_one_tool() 之前调用 approval_gate
- `agent/runtime/agent_app.py` 把频道内的 approve/deny 回复路由到批准管理器
- `BaseChannel.render_approval_prompt()` 由各频道实现具体渲染
"""

from .gate import ApprovalDenied, ApprovalGate, get_approval_gate
from .guardian import ApprovalGuardian, GuardianVerdict, get_approval_guardian
from .manager import ApprovalManager, get_approval_manager
from .policy import ApprovalPolicy, ApprovalPolicySet, RiskLevel
from .rules import (
    PermissionDecision,
    PermissionEffect,
    PermissionRule,
    PermissionRuleSet,
    PermissionVerdict,
    load_rules,
    save_rules,
)
from .session import ApprovalDecision, ApprovalRequest, ApprovalSession

__all__ = [
    "ApprovalDecision",
    "ApprovalDenied",
    "ApprovalGate",
    "ApprovalGuardian",
    "ApprovalManager",
    "ApprovalPolicy",
    "ApprovalPolicySet",
    "ApprovalRequest",
    "ApprovalSession",
    "GuardianVerdict",
    "PermissionDecision",
    "PermissionEffect",
    "PermissionRule",
    "PermissionRuleSet",
    "PermissionVerdict",
    "RiskLevel",
    "get_approval_gate",
    "get_approval_guardian",
    "get_approval_manager",
    "load_rules",
    "save_rules",
]
