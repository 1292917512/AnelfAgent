"""批准机制 — 工具调用前的人工确认门（统一权限引擎）。

- **rules.py**: PermissionRule/PermissionRuleSet — 统一权限规则模型与求值引擎
  （``工具名(参数glob)`` + allow/ask/deny + global/频道 scope，单一求值管线）
- **policy.py**: 旧 ApprovalPolicy（保留兼容，新规则引擎可自动转换）
- **session.py**: ApprovalSession 表示一次挂起的批准请求
- **gate.py**: ApprovalGate 是入口，负责求值、创建 session、等待用户决策、记住决策
- **renderer.py**: 各频道渲染抽象（默认文本，子类可覆盖为按钮/卡片/SSE 弹窗）
- **manager.py**: ApprovalManager 全局管理所有挂起的批准请求

集成点：
- `agent/mind/tools/think_loop.py` 在 execute_one_tool() 之前调用 approval_gate
- `agent/runtime/agent_app.py` 把频道内的 approve/deny 回复路由到批准管理器
- `BaseChannel.render_approval_prompt()` 由各频道实现具体渲染
"""

from .gate import ApprovalDenied, ApprovalGate, get_approval_gate
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
    "ApprovalManager",
    "ApprovalPolicy",
    "ApprovalPolicySet",
    "ApprovalRequest",
    "ApprovalSession",
    "PermissionDecision",
    "PermissionEffect",
    "PermissionRule",
    "PermissionRuleSet",
    "PermissionVerdict",
    "RiskLevel",
    "get_approval_gate",
    "get_approval_manager",
    "load_rules",
    "save_rules",
]
