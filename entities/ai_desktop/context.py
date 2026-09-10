"""AI 桌面上下文提供者 — 把启用组件的实时信息注入 PFC volatile 层。

单一 provider 聚合全部桌面组件：后台循环按各组件自身间隔调度 refresh
（轮询型组件的网络 I/O 在此完成），provide() 只经 framework.render_context
读取缓存快照拼装注入块（零 I/O，满足 provider 1s 超时约束）。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from core.context_provider import ProviderSnapshot
from entities._sdk import context_provider

from . import framework

_SCHED_TICK_SECONDS = 5.0


@context_provider(
    name="ai_desktop", priority=50, max_tokens=400,
    group="ai_desktop", inject_key="ai_desktop_context_inject",
)
class AiDesktopProvider:
    """注入桌面环境动态信息（时间/节日/天气等启用组件的聚合快照）。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None

    async def on_start(self) -> None:
        await framework.refresh_due()
        self._task = asyncio.create_task(self._refresh_loop())

    async def on_stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(_SCHED_TICK_SECONDS)
            await framework.refresh_due()

    async def provide(self, scope: str) -> Optional[ProviderSnapshot]:
        content = framework.render_context()
        if not content:
            return None
        return ProviderSnapshot(content=content, ready=True)
