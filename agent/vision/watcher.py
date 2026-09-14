"""盯屏 watcher：按源驱动的定频捕获循环。

惰性启动（首个 vision_watch(start) 才拉起任务，不用零成本）；
每个轮询型源一个任务，连续失败指数退避（5s→60s 封顶）；
注册 Lifecycle 清理钩子保证关停/热拔除时全部收束。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from core.config import get_config_float
from core.log import log

from .buffer import get_vision_buffer
from .framework import VisualSource, get_source

_LOG_TAG = "视觉"


class VisionWatcher:
    """视觉源监视循环（进程内单例，经 get_vision_watcher 获取）。"""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}
        self._lifecycle_registered = False
        self._stats: Dict[str, Dict[str, Any]] = {}

    def watching(self, source_key: str) -> bool:
        task = self._tasks.get(source_key)
        return task is not None and not task.done()

    def watching_sources(self) -> list[str]:
        return sorted(k for k in self._tasks if self.watching(k))

    async def start(self, source_key: str) -> Optional[str]:
        """开始监视指定源；源不存在/已停用/不支持轮询时返回错误文案（成功 None）。"""
        from .framework import is_enabled
        source = get_source(source_key)
        if source is None:
            return f"视觉源不存在: {source_key}"
        if not is_enabled(source_key):
            return f"视觉源已停用: {source_key}（先在视觉页签或经 vision_source_set 激活）"
        if not source.can_capture or source.poll_interval <= 0:
            return f"视觉源 {source_key} 不支持轮询监视（即时型/外部推送源）"
        if self.watching(source_key):
            return None
        self._register_lifecycle()
        self._stats[source_key] = {
            "started_at": time.time(), "last_capture_at": 0.0,
            "last_error": "", "capture_count": 0,
        }
        self._tasks[source_key] = asyncio.create_task(
            self._loop(source), name=f"vision.watcher.{source_key}",
        )
        log(f"视觉监视已开始: {source_key}", "DEBUG", tag=_LOG_TAG)
        return None

    async def stop(self, source_key: str) -> None:
        """停止监视指定源（幂等）。"""
        task = self._tasks.pop(source_key, None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        log(f"视觉监视已停止: {source_key}", "DEBUG", tag=_LOG_TAG)

    async def stop_all(self) -> None:
        """停止全部监视（Lifecycle 清理钩子）。"""
        for key in list(self._tasks):
            await self.stop(key)

    def status(self) -> Dict[str, Any]:
        return {
            "watching": self.watching_sources(),
            "sources": {k: dict(v) for k, v in self._stats.items()},
        }

    def source_status(self, source_key: str) -> Dict[str, Any]:
        return dict(self._stats.get(source_key, {}))

    def _register_lifecycle(self) -> None:
        if self._lifecycle_registered:
            return
        from core.lifecycle import Lifecycle
        Lifecycle.register("vision.watcher", self, cleanup=self.stop_all)
        self._lifecycle_registered = True

    async def _loop(self, source: VisualSource) -> None:
        consecutive_errors = 0
        stats = self._stats[source.key]
        while True:
            # 源被注销（组件热拔除）即退出：任务不得围着孤儿源空转
            if get_source(source.key) is not source:
                log(f"视觉源已注销，监视循环退出: {source.key}", "DEBUG", tag=_LOG_TAG)
                return
            interval = get_config_float("vision_watch_interval_s", 5.0)
            try:
                frame = await source.capture()
                if frame is not None:
                    _f, changed = await get_vision_buffer().ingest(
                        frame.path, source.key,
                        width=frame.width, height=frame.height,
                        captured_at=frame.captured_at,
                    )
                    stats["capture_count"] += 1
                    stats["last_capture_at"] = time.time()
                    if consecutive_errors:
                        log(f"视觉捕获恢复: {source.key}（此前连续失败 {consecutive_errors} 次）",
                            "INFO", tag=_LOG_TAG)
                        consecutive_errors = 0
                        stats["last_error"] = ""
                    if changed:
                        log(f"画面有变化: {source.key}", "DEBUG", tag=_LOG_TAG)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 捕获失败（无权限/无显示/休眠）不杀循环：连续失败指数退避
                consecutive_errors += 1
                stats["last_error"] = str(exc)
                backoff = min(60.0, interval * (2 ** min(consecutive_errors - 1, 4)))
                log(
                    f"视觉捕获失败: {source.key}（第 {consecutive_errors} 次，{backoff:.0f}s 后重试）: {exc}",
                    "WARNING", tag=_LOG_TAG,
                )
                await asyncio.sleep(backoff)
                continue
            await asyncio.sleep(max(1.0, interval))


_watcher: Optional[VisionWatcher] = None


def get_vision_watcher() -> VisionWatcher:
    """进程内单例。"""
    global _watcher
    if _watcher is None:
        _watcher = VisionWatcher()
    return _watcher
