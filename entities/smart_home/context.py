"""智能家居上下文提供者 — 把启用设备域的实时状态注入 PFC volatile 层。

设备状态经 WebSocket 实时推入缓存，provide() 只读内存快照（零 I/O，
满足 provider 1s 超时约束）：未配置平台 / 全部设备域无可用设备时返回
None 不注入；已配置但连接中断时注入一行不可用提示（AI 可感知而非静默）。
"""

from __future__ import annotations

from typing import Optional

from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from . import framework
from .manager import get_smart_home_manager


@context_provider(
    name="smart_home", priority=50, max_tokens=600,
    group="smart_home", inject_key="smart_home_context_inject",
)
class SmartHomeContextProvider:
    """注入智能家居设备实时状态（启用设备域的聚合快照）。"""

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        manager = get_smart_home_manager()
        status = manager.status()
        if not status.get("configured"):
            return None
        if not status.get("connected"):
            return ProviderSnapshot(
                content="[智能家居] 与 Home Assistant 连接中断，设备状态不可用",
                ready=True,
            )
        content = framework.render_context(manager.devices())
        if not content:
            return None
        return ProviderSnapshot(content=content, ready=True)
