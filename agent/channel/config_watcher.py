"""配置文件热更新监听 — 自动检测配置变更并重载。

使用 watchdog 监听配置文件变更，自动触发 reload_config()。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable, Dict, Optional

from core.log import log


class ConfigWatcher:
    """配置文件监听器（单例）。"""

    def __init__(self) -> None:
        self._watchers: Dict[str, asyncio.Task] = {}
        self._callbacks: Dict[str, Callable[[], None]] = {}
        self._running = False

    def watch(self, file_path: str, callback: Callable[[], None]) -> None:
        """监听配置文件变更。

        Args:
            file_path: 配置文件路径
            callback: 变更时调用的回调函数（同步）
        """
        if file_path in self._watchers:
            log(f"配置文件已在监听: {file_path}", "DEBUG", tag="配置")
            return

        self._callbacks[file_path] = callback
        # 延迟创建 task，避免在同步上下文中创建 coroutine
        try:
            loop = asyncio.get_running_loop()
            self._watchers[file_path] = loop.create_task(
                self._watch_loop(file_path),
                name=f"config_watcher.{Path(file_path).stem}",
            )
            log(f"配置文件监听已启动: {file_path}", tag="配置")
        except RuntimeError:
            # 没有运行中的事件循环，跳过（将在首次访问时启动）
            log(f"配置文件监听延迟启动（无事件循环）: {file_path}", "DEBUG", tag="配置")

    def unwatch(self, file_path: str) -> None:
        """停止监听配置文件。"""
        task = self._watchers.pop(file_path, None)
        if task and not task.done():
            task.cancel()
        self._callbacks.pop(file_path, None)
        log(f"配置文件监听已停止: {file_path}", tag="配置")

    async def _watch_loop(self, file_path: str) -> None:
        """监听循环（轮询 mtime）。"""
        last_mtime = self._get_mtime(file_path)
        while True:
            await asyncio.sleep(1.0)  # 每秒检查一次
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
