"""批准机制 — 工具调用前的人工确认门。

借鉴 OpenClaw approval-* 系列 packages 的设计，结合 AnelfAgent 现有架构：

- **policy.py**: ApprovalPolicy 声明哪些工具需要批准（glob 匹配 + 风险等级）
- **session.py**: ApprovalSession 表示一次挂起的批准请求
- **gate.py**: ApprovalGate 是入口，负责创建 session、等待用户决策
- **renderer.py**: 各频道渲染抽象（默认文本，子类可覆盖为按钮/卡片）
- **manager.py**: ApprovalManager 全局管理所有挂起的批准请求

集成点：
- `agent/mind/tool_activation.py` 在 activate_tool() 之前调用 approval_gate
- `agent/mind/guardrails.py` 检测到 critical 风险时可升级到 approval
- `BaseChannel.render_approval_prompt()` 由各频道实现具体渲染
"""

from .gate import ApprovalGate, get_approval_gate
from .manager import ApprovalManager, get_approval_manager
from .policy import ApprovalPolicy, ApprovalPolicySet, RiskLevel
from .session import ApprovalDecision, ApprovalRequest, ApprovalSession

__all__ = [
    "ApprovalDecision",
    "ApprovalGate",
    "ApprovalManager",
    "ApprovalPolicy",
    "ApprovalPolicySet",
    "ApprovalRequest",
    "ApprovalSession",
    "RiskLevel",
    "get_approval_gate",
    "get_approval_manager",
]
