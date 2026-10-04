"""SSH 上下文提供者 — 将 SSH 连接花名册与操作态势注入 PFC volatile 层。

两个 provider 分工：
- ssh_status：全局连接花名册（含离线连接）——可用连接、登录用户@主机、
  缺省执行目标、在线连接的当前远程目录与异常标记，AI 无需 ssh_list
  即可感知远程环境，首次调用即可填对 name 与路径前提；
- ssh_ops：按会话隔离的操作态势——只展示本会话近期操作过的主机
  （远程目录 / 目录说明文档 / 最近操作流水），停止操作超时后自动消失。

快照直接读管理器与态势追踪器的内存状态（零 I/O），变更即时反映。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from . import ops_state
from .manager import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_ERROR,
    get_ssh_manager,
)


def _render_roster_line(status: Dict[str, Any]) -> str:
    """渲染单条连接的花名册行：身份 + 状态标记（缺省/在线/目录/异常）+ 描述。"""
    port = int(status.get("port", 22) or 22)
    port_part = f":{port}" if port != 22 else ""
    line = f"- {status['name']}: {status.get('username', '')}@{status.get('host', '')}{port_part}"
    marks: List[str] = []
    if status.get("is_default"):
        marks.append("缺省")
    state = status.get("status", "")
    if state == STATUS_CONNECTED:
        marks.append("在线")
        work_dir = status.get("work_dir") or ""
        if work_dir:
            marks.append(f"目录 {work_dir}")
    elif state == STATUS_CONNECTING:
        marks.append("连接中")
    elif state == STATUS_ERROR:
        error = (status.get("last_error") or "").replace("\n", " ").strip()
        marks.append(f"连接失败: {error[:60]}" if error else "连接失败")
    if status.get("home_missing"):
        marks.append("登录目录缺失，命令起点 /")
    if marks:
        line += f"（{' · '.join(marks)}）"
    desc = (status.get("description") or "").replace("\n", " ").strip()
    if desc:
        line += f" - {desc}"
    return line


@context_provider(
    name="ssh_status", priority=16, max_tokens=500,
    group="ssh", inject_key="ssh_context_inject",
)
class SshStatusProvider:
    """注入 SSH 连接花名册（含离线）与缺省执行目标。"""

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        statuses = get_ssh_manager().list_statuses()
        if not statuses:
            return None

        lines = [
            "[SSH 远程连接] 可用连接（name 缺省作用于缺省连接，离线连接首次执行自动建连）:",
        ]
        lines.extend(_render_roster_line(s) for s in statuses)
        return ProviderSnapshot(content="\n".join(lines), ready=True)


@context_provider(
    name="ssh_ops", priority=30, max_tokens=2000,
    group="ssh", inject_key="ssh_ops_context_inject",
)
class SshOpsProvider:
    """SSH 操作态势（本会话操作的主机 / 远程目录与说明文档 / 最近操作）。"""

    async def provide(self, scope: str) -> Optional[str]:
        return ops_state.render_scope(scope)
