"""SSH 上下文提供者 — 将 SSH 连接状态与操作态势注入 PFC volatile 层。

两个 provider 分工：
- ssh_status：全局在线主机花名册（AI 无需 ssh_list 即可感知远程环境）；
- ssh_ops：按会话隔离的操作态势——只展示本会话近期操作过的主机
  （远程目录 / 目录说明文档 / 最近操作流水），停止操作超时后自动消失。

快照直接读管理器与态势追踪器的内存状态（零 I/O），变更即时反映。
"""

from __future__ import annotations

from typing import Optional

from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from . import ops_state
from .manager import STATUS_CONNECTED, get_ssh_manager


@context_provider(
    name="ssh_status", priority=16, max_tokens=200,
    group="ssh", inject_key="ssh_context_inject",
)
class SshStatusProvider:
    """注入当前已连接的 SSH 主机清单。"""

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        manager = get_ssh_manager()
        active = [
            s for s in manager.list_statuses()
            if s.get("status") == STATUS_CONNECTED
        ]
        if not active:
            return None

        default_name = next(
            (s["name"] for s in active if s.get("is_default")), "",
        )
        lines = ["[SSH 远程连接] 当前在线:"]
        for s in active:
            mark = "（默认）" if s.get("is_default") else ""
            host = s.get("host", "")
            user = s.get("username", "")
            desc = s.get("description", "")
            desc_part = f" - {desc}" if desc else ""
            lines.append(f"- {s['name']}: {user}@{host}{mark}{desc_part}")
        if default_name:
            lines.append(f"缺省执行目标: {default_name}")

        content = "\n".join(lines)
        return ProviderSnapshot(content=content, ready=True)


@context_provider(
    name="ssh_ops", priority=30, max_tokens=2000,
    group="ssh", inject_key="ssh_ops_context_inject",
)
class SshOpsProvider:
    """SSH 操作态势（本会话操作的主机 / 远程目录与说明文档 / 最近操作）。"""

    async def provide(self, scope: str) -> Optional[str]:
        return ops_state.render_scope(scope)
