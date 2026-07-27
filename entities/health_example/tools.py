"""健康数据示例 — Context Provider 完整生命周期演示。

实体开发者只需关注：
1. __init__：初始化快照（ready=False）
2. on_start：起 RunTimeline（后台采集循环）
3. provide：返回快照（零 I/O，只读内存）
4. on_tick：可选，心跳 tick 时触发
5. on_stop：cancel 后台 task
"""

from __future__ import annotations

import asyncio
import random
import time

from entities._sdk import context_provider, entity
from core.context_provider import ProviderSnapshot

entity("health_example", "健康数据示例 - Context Provider 生命周期演示")


@context_provider(name="health_example", priority=10, max_tokens=150)
class HealthExampleProvider:
    """模拟用户实时健康数据采集器。

    每 5 秒更新一次内部快照，PFC 每轮构建 volatile 层时
    通过 provide() 拉取最新数据注入 LLM 上下文。
    """

    def __init__(self) -> None:
        self._snapshot = ProviderSnapshot(
            content=None,
            ready=False,
            default_when_not_ready="[健康数据] 传感器初始化中…",
        )
        self._timeline: asyncio.Task | None = None
        self._tick_count = 0

    async def on_start(self) -> None:
        """bootstrap 末尾调用：启动后台采集循环。"""
        self._timeline = asyncio.create_task(self._run_timeline())

    async def _run_timeline(self) -> None:
        """实体自驱的 RunTimeline：采集 → 更新快照 → sleep → 循环。"""
        # 模拟传感器初始化延迟
        await asyncio.sleep(1.0)

        while True:
            try:
                # 模拟采集（真实场景替换为传感器 API 调用）
                heart_rate = random.randint(60, 100)
                spo2 = random.randint(95, 100)
                steps = random.randint(0, 15000)

                content = (
                    f"[实时健康] 心率 {heart_rate}bpm · "
                    f"血氧 {spo2}% · 今日步数 {steps}"
                )
                self._snapshot = ProviderSnapshot(
                    content=content,
                    ready=True,
                    tokens=len(content) // 4 + 1,
                    bytes=len(content.encode("utf-8")),
                    fetched_at=time.time(),
                )
            except Exception:
                pass  # 采集失败保持旧快照
            await asyncio.sleep(5.0)

    async def provide(self, scope: str) -> ProviderSnapshot | None:
        """PFC 每轮拉取：零 I/O，只读快照。"""
        return self._snapshot

    async def on_tick(self) -> None:
        """心跳 tick 时触发（可选）：这里仅计数演示。"""
        self._tick_count += 1

    async def on_stop(self) -> None:
        """shutdown 时调用：取消后台 task。"""
        if self._timeline is not None:
            self._timeline.cancel()
            try:
                await self._timeline
            except asyncio.CancelledError:
                pass
            self._timeline = None
