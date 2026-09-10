"""Dify 上下文提供者 — 把 Dify 连接状态摘要注入 PFC volatile 层。

AI 每轮推理即可感知"Dify 是否已连接、入口地址、纳管应用数"，
无需主动调用 dify_status。快照全部读本地内存状态（密钥库缓存），
零网络 I/O；注入开关为实体配置 dify_context_inject。
"""

from __future__ import annotations

from typing import Optional

from core.config import get_config, get_config_bool
from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from .config import get_dify_store


@context_provider(
    name="dify_status", priority=12, max_tokens=160,
    group="dify", inject_key="dify_context_inject",
)
class DifyStatusProvider:
    """注入 Dify 连接状态摘要（仅在已配置地址且存有凭据时注入）。"""

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        if not get_config_bool("dify_enabled", True):
            return None
        base_url = str(get_config("dify_base_url", "") or "").rstrip("/")
        if not base_url:
            return None

        store = get_dify_store()
        admin = store.get_admin()
        if not (admin.get("email") and admin.get("password")):
            return None

        apps = store.list_apps()
        mcp_count = sum(1 for a in apps.values() if a.get("mcp_server_code"))

        lines = [f"[Dify 平台] 已连接 {base_url}"]
        if apps:
            names = "、".join(str(a.get("name") or app_id[:8]) for app_id, a in list(apps.items())[:8])
            lines.append(f"纳管应用 {len(apps)} 个: {names}")
            if mcp_count:
                lines.append(f"其中 {mcp_count} 个已桥接为 MCP 工具（可直接调用）")
        lines.append("调用 dify_* 工具可管理应用/工作流；未激活时先 activate_tool_group(\"dify\")")

        return ProviderSnapshot(content="\n".join(lines), ready=True)
