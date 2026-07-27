"""轻量级单例注册表 — 统一管理全局单例的创建与销毁。

启动时通过 ``Lifecycle.register()`` 注册组件及其 cleanup 回调，
关闭时通过 ``Lifecycle.shutdown_all()`` 逆序执行所有回调，确保资源正确释放。

扩展生命周期钩子：
- ``on_start``：bootstrap 末尾由 ``start_all()`` 正序触发
- ``on_tick``：心跳 tick 时由 ``tick_all()`` 触发
- ``cleanup``（即 on_stop）：shutdown 时由 ``shutdown_all()`` 逆序触发
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from core.log import log

CleanupFn = Union[Callable[[], None], Callable[[], Awaitable[None]]]
HookFn = Union[Callable[[], None], Callable[[], Awaitable[None]]]


class Lifecycle:
    """全局单例生命周期管理。"""

    _instances: Dict[str, Any] = {}
    _cleanups: List[tuple[str, CleanupFn]] = []
    _start_hooks: List[tuple[str, HookFn]] = []
    _tick_hooks: List[tuple[str, HookFn]] = []

    @classmethod
    def register(
        cls,
        name: str,
        instance: Any,
        cleanup: Optional[CleanupFn] = None,
        on_start: Optional[HookFn] = None,
        on_tick: Optional[HookFn] = None,
    ) -> None:
        """注册单例实例，可选附带 cleanup / on_start / on_tick 回调。

        同名重复注册时替换旧实例并丢弃旧回调，避免关闭时重复清理。
        """
        cls._instances[name] = instance
        if cleanup:
            cls._cleanups = [(n, fn) for n, fn in cls._cleanups if n != name]
            cls._cleanups.append((name, cleanup))
        if on_start:
            cls._start_hooks = [(n, fn) for n, fn in cls._start_hooks if n != name]
            cls._start_hooks.append((name, on_start))
        if on_tick:
            cls._tick_hooks = [(n, fn) for n, fn in cls._tick_hooks if n != name]
            cls._tick_hooks.append((name, on_tick))

    @classmethod
    def get(cls, name: str) -> Optional[Any]:
        """按名称获取已注册实例。"""
        return cls._instances.get(name)

    @classmethod
    async def start_all(cls) -> None:
        """正序执行所有 on_start 钩子（bootstrap 末尾调用）。"""
        for name, fn in cls._start_hooks:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
            except Exception as e:
                log(f"启动钩子失败: {name} - {e}", "WARNING")

    @classmethod
    async def tick_all(cls) -> None:
        """心跳 tick 时调用所有 on_tick 钩子。"""
        for name, fn in cls._tick_hooks:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
            except Exception as e:
                log(f"tick 钩子失败: {name} - {e}", "DEBUG")

    @classmethod
    async def shutdown_all(cls) -> None:
        """逆序执行所有 cleanup 回调，释放资源。"""
        for name, fn in reversed(cls._cleanups):
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn()
                else:
                    fn()
                log(f"已清理: {name}")
            except Exception as e:
                log(f"清理失败: {name} - {e}", "WARNING")
        cls._instances.clear()
        cls._cleanups.clear()
        cls._start_hooks.clear()
        cls._tick_hooks.clear()

    @classmethod
    def reset(cls) -> None:
        """重置所有注册（测试用）。"""
        cls._instances.clear()
        cls._cleanups.clear()
        cls._start_hooks.clear()
        cls._tick_hooks.clear()
