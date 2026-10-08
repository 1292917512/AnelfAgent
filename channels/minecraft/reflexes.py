"""本地生存反应的配置同步与异步事实播报，不在 Python 中控制游戏动作。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from core.log import log
from core.tool_context import tool_request

from .protocol import SurvivalProgress, SurvivalStatus

MCPToolCall = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Announce = Callable[[str], Awaitable[None]]


class SurvivalBridge:
    """复用连接快照同步本地设置；有界播报队列不阻塞聊天和停止分发。"""

    def __init__(self, call: MCPToolCall, announce: Announce, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._call = call
        self._announce = announce
        self._clock = clock
        self._retry_at = 0.0
        self._runtime_id = ""
        self._seen: dict[tuple[str, str], None] = {}
        self._notices: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=32)
        self._config_task: asyncio.Task[None] | None = None
        self._notice_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._notice_task is None or self._notice_task.done():
            self._notice_task = asyncio.create_task(self._deliver(), name="channel.minecraft.survival-notices")

    async def stop(self) -> None:
        tasks = [task for task in (self._notice_task, self._config_task) if task is not None]
        self._notice_task = self._config_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        while not self._notices.empty():
            self._notices.get_nowait()
            self._notices.task_done()
        self._runtime_id = ""
        self._seen.clear()
        self._retry_at = 0.0

    def sync(self, status: SurvivalStatus | None, *, enabled: bool, interval_seconds: float, scope: str) -> None:
        """只在设置不符或执行器重启时写配置，不追加任何观察 RPC。"""
        if status is None:
            return
        if status.runtime_id != self._runtime_id:
            self._runtime_id = status.runtime_id
            self._retry_at = 0.0
            self._seen.clear()
        interval_ms = round(interval_seconds * 1000)
        if (status.enabled, status.interval_ms) == (enabled, interval_ms):
            return
        if self._config_task is not None and not self._config_task.done():
            return
        if self._clock() < self._retry_at:
            return
        self._retry_at = self._clock() + 5.0
        with tool_request(scope, "@reflex"):
            self._config_task = asyncio.create_task(
                self._configure(enabled, interval_ms), name="channel.minecraft.survival-config",
            )

    async def _configure(self, enabled: bool, interval_ms: int) -> None:
        try:
            result = await self._call("configure_survival", {"enabled": enabled, "intervalMs": interval_ms})
            SurvivalStatus.model_validate(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"Minecraft 本地生存配置未同步: {exc}", "WARNING", tag="Minecraft")

    def event(self, data: Any) -> None:
        try:
            progress = SurvivalProgress.model_validate(data)
        except ValidationError:
            log("Minecraft 忽略非法生存事件", "WARNING", tag="Minecraft")
            return
        if progress.runtime_id != self._runtime_id:
            return
        key = (progress.id, progress.phase)
        if key in self._seen:
            return
        self._seen[key] = None
        if len(self._seen) > 128:
            self._seen.pop(next(iter(self._seen)))
        text = progress.announcement()
        if text:
            self.announce(text)

    def announce(self, text: str) -> None:
        self.start()
        if self._notices.full():
            self._notices.get_nowait()
            self._notices.task_done()
            log("Minecraft 事实播报积压，已丢弃最旧播报；执行器事件仍可查询", "WARNING", tag="Minecraft")
        self._notices.put_nowait((self._runtime_id, text))

    async def _deliver(self) -> None:
        while True:
            runtime_id, text = await self._notices.get()
            try:
                if runtime_id == self._runtime_id:
                    await self._announce(text)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"Minecraft 事实播报失败: {exc}", "WARNING", tag="Minecraft")
            finally:
                self._notices.task_done()
