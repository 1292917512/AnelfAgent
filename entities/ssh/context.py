"""SSH 上下文提供者 — 将当前激活的 SSH 连接注入 PFC volatile 层。

AI 每轮推理时即可感知"哪些远程主机当前在线、默认连接是哪台"，
无需主动调用 ssh_list 就能自然地引用远程环境。
快照直接读管理器内存状态（零 I/O），连接池变更即时反映。
"""

from __future__ import annotations

from typing import Optional

from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from .manager import STATUS_CONNECTED, get_ssh_manager


@context_provider(name="ssh_status", priority=30, max_tokens=200, group="ssh")
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
