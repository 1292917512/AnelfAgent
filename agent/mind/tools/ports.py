"""思维工具组共享的运行时端口。

scheduler / session_tools / short_term_tools 的工具经 deferred_tool 在
import 时注册，而 Mind 要到 bootstrap 组装阶段才构造（Mind 初始化本身
会激活 thinking/session 工具组，构成循环初始化），因此经 LateBinding
端口分发 Mind 引用 —— 准入规则见 AGENTS.md 开发约定「晚绑定」。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.latebind import LateBinding

if TYPE_CHECKING:
    from agent.mind.mind import Mind

#: Mind 实例端口（三个工具模块共用，bootstrap 经 agent.runtime.wiring 施绑）
mind_port: LateBinding["Mind"] = LateBinding("mind.tools")
