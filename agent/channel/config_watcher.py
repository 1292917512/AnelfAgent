"""配置文件热更新监听 — 自动检测配置变更并重载。

实现方式：每个被监听文件一个 asyncio 轮询任务，按固定间隔（默认 1s）
比较 os.path.getmtime，变更时触发回调（未使用 watchdog 等第三方库）。
另支持目录结构监听（watch_dir）：轮询子目录与标记文件的存在性快照，
增删变化防抖后触发回调（模块热插拔的自动触发源）。

无事件循环时 watch() 仅登记回调，任务延迟到 ensure_started()
（在频道启动路径由 ChannelManager 调用）首次获得有效事件循环时启动。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from core.log import log

# mtime 轮询间隔（秒）
POLL_INTERVAL_SECONDS = 1.0

# 目录结构变化防抖轮询数：变化后连续一次轮询无新变化才触发回调
# （目录增删常伴随批量文件操作，合并为一次同步）
_DIR_DEBOUNCE_POLLS = 2


class ConfigWatcher:
    """配置文件监听器（单例，mtime 轮询实现）。"""

    def __init__(self) -> None:
        self._watchers: Dict[str, asyncio.Task] = {}
        self._callbacks: Dict[str, Callable[[], None]] = {}
        # 目录监听：path -> (callback, 标记文件名)
        self._dir_watchers: Dict[str, asyncio.Task] = {}
        self._dir_callbacks: Dict[str, Tuple[Callable[[], None], str]] = {}

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

        供无事件循环期间登记的监听在首个有效事件循环可用时补启动；
        当前生产调用点（approval_policies / hooks）均在异步启动节点内登记，
        正常路径下 watch() 即已即时启动，本方法为兜底。
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        for file_path in self._callbacks:
            task = self._watchers.get(file_path)
            if task is None or task.done():
                self._start_task(file_path)
        for dir_path in self._dir_callbacks:
            task = self._dir_watchers.get(dir_path)
            if task is None or task.done():
                self._start_dir_task(dir_path)

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

    def watch_dir(self, dir_path: str, callback: Callable[[], None], marker: str = "") -> None:
        """监听目录结构变化（子目录增删 / 标记文件出现或消失），防抖后回调。

        Args:
            dir_path: 目录路径。
            callback: 结构稳定变化后调用的回调（同步）。
            marker: 子目录内的标记文件名（如 tools.py / adapter.py），
                标记文件的出现/消失同样视为结构变化；空串只盯子目录增删。
        """
        if dir_path in self._dir_callbacks:
            log(f"目录已在监听: {dir_path}", "DEBUG", tag="配置")
            return
        self._dir_callbacks[dir_path] = (callback, marker)
        # 与文件监听同一 Lifecycle 条目（幂等）
        from core.lifecycle import Lifecycle
        Lifecycle.register("config_watcher", self, cleanup=self.stop_all)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            log(f"目录监听已登记（待事件循环启动）: {dir_path}", "DEBUG", tag="配置")
            return
        self._start_dir_task(dir_path)

    def unwatch_dir(self, dir_path: str) -> None:
        """停止监听目录结构。"""
        task = self._dir_watchers.pop(dir_path, None)
        if task and not task.done():
            task.cancel()
        self._dir_callbacks.pop(dir_path, None)
        log(f"目录监听已停止: {dir_path}", tag="配置")

    def _start_dir_task(self, dir_path: str) -> None:
        self._dir_watchers[dir_path] = asyncio.create_task(
            self._dir_watch_loop(dir_path),
            name=f"config_watcher.dir.{Path(dir_path).name}",
        )
        log(f"目录监听已启动: {dir_path}", tag="配置")

    @staticmethod
    def _dir_snapshot(dir_path: str, marker: str) -> Dict[str, bool]:
        """目录结构快照：{子目录名: 标记文件是否存在}（无标记时恒 True）。"""
        snapshot: Dict[str, bool] = {}
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    if not entry.is_dir() or entry.name.startswith("_"):
                        continue
                    snapshot[entry.name] = (
                        os.path.exists(os.path.join(entry.path, marker)) if marker else True
                    )
        except OSError:
            pass
        return snapshot

    async def _dir_watch_loop(self, dir_path: str) -> None:
        """目录监听循环（轮询结构快照，变化防抖后触发回调）。"""
        entry = self._dir_callbacks.get(dir_path)
        last = self._dir_snapshot(dir_path, entry[1] if entry else "")
        quiet_polls = 0
        pending = False
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            entry = self._dir_callbacks.get(dir_path)
            if entry is None:
                return
            callback, marker = entry
            current = self._dir_snapshot(dir_path, marker)
            if current != last:
                last = current
                pending = True
                quiet_polls = 0
                continue
            if not pending:
                continue
            quiet_polls += 1
            if quiet_polls < _DIR_DEBOUNCE_POLLS:
                continue
            pending = False
            try:
                callback()
                log(f"目录结构变化已处理: {dir_path}", tag="配置")
            except Exception as exc:
                log(f"目录变化回调失败: {dir_path}: {exc}", "ERROR", tag="配置")

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
        for task in self._dir_watchers.values():
            if not task.done():
                task.cancel()
        self._dir_watchers.clear()
        self._dir_callbacks.clear()
        log("所有配置文件监听已停止", tag="配置")


# 全局单例
_watcher: Optional[ConfigWatcher] = None


def get_config_watcher() -> ConfigWatcher:
    """获取全局配置监听器。"""
    global _watcher
    if _watcher is None:
        _watcher = ConfigWatcher()
    return _watcher
