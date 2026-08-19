"""配置文件热更新监听 — 自动检测配置变更并重载。

实现方式：每个被监听文件一个 asyncio 轮询任务，按固定间隔（默认 1s）
比较 os.path.getmtime，变更时触发回调（未使用 watchdog 等第三方库）。

无事件循环时 watch() 仅登记回调，任务延迟到 ensure_started()
（在频道启动路径由 ChannelManager 调用）首次获得有效事件循环时启动。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable, Dict, Optional

from core.log import log

# mtime 轮询间隔（秒）
POLL_INTERVAL_SECONDS = 1.0


class ConfigWatcher:
    """配置文件监听器（单例，mtime 轮询实现）。"""

    def __init__(self) -> None:
        self._watchers: Dict[str, asyncio.Task] = {}
        self._callbacks: Dict[str, Callable[[], None]] = {}

    def watch(self, file_path: str, callback: Callable[[], None]) -> None:
        """监听配置文件变更。

        Args:
            file_path: 配置文件路径
            callback: 变更时调用的回调函数（同步）
        """
        if file_path in self._callbacks:
            log(f"配置文件已在监听: {file_path}", "DEBUG", tag="配置")
            return

        self._callbacks[file_path] = callback
        # 首个监听注册时接入 Lifecycle 宿主，关停时统一回收轮询任务（幂等）
        from core.lifecycle import Lifecycle
        Lifecycle.register("config_watcher", self, cleanup=self.stop_all)
        # 延迟创建 task，避免在同步上下文中创建 coroutine
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行中的事件循环：仅登记回调，
            # 由 ensure_started() 在首个有效事件循环可用时启动轮询任务
            log(f"配置文件监听已登记（待事件循环启动）: {file_path}", "DEBUG", tag="配置")
            return
        self._start_task(file_path)

    def ensure_started(self) -> None:
        """为所有已登记但尚未启动的监听创建轮询任务（幂等）。

        在频道启动路径（ChannelManager.start_channel / start_all）调用，
        保证无事件循环期间登记的监听最终都会生效。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        for file_path in self._callbacks:
            task = self._watchers.get(file_path)
            if task is None or task.done():
                self._start_task(file_path)

    def _start_task(self, file_path: str) -> None:
        """在当前事件循环中创建单个文件的轮询任务。"""
        self._watchers[file_path] = asyncio.create_task(
            self._watch_loop(file_path),
            name=f"config_watcher.{Path(file_path).stem}",
        )
        log(f"配置文件监听已启动: {file_path}", tag="配置")

    def unwatch(self, file_path: str) -> None:
        """停止监听配置文件。"""
        task = self._watchers.pop(file_path, None)
        if task and not task.done():
            task.cancel()
        self._callbacks.pop(file_path, None)
        log(f"配置文件监听已停止: {file_path}", tag="配置")

    async def _watch_loop(self, file_path: str) -> None:
        """监听循环（按 POLL_INTERVAL_SECONDS 轮询 mtime）。"""
        last_mtime = self._get_mtime(file_path)
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            current_mtime = self._get_mtime(file_path)
            if current_mtime > last_mtime:
                last_mtime = current_mtime
                callback = self._callbacks.get(file_path)
                if callback:
                    try:
                        callback()
                        log(f"配置文件已重载: {file_path}", tag="配置")
                    except Exception as exc:
                        log(f"配置文件重载失败: {file_path}: {exc}", "ERROR", tag="配置")

    @staticmethod
    def _get_mtime(file_path: str) -> float:
        """获取文件修改时间。"""
        try:
            return os.path.getmtime(file_path)
        except OSError:
            return 0.0

    def stop_all(self) -> None:
        """停止所有监听。"""
        for task in self._watchers.values():
            if not task.done():
                task.cancel()
        self._watchers.clear()
        self._callbacks.clear()
        log("所有配置文件监听已停止", tag="配置")


# 全局单例
_watcher: Optional[ConfigWatcher] = None


def get_config_watcher() -> ConfigWatcher:
    """获取全局配置监听器。"""
    global _watcher
    if _watcher is None:
        _watcher = ConfigWatcher()
    return _watcher
