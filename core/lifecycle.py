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

# 与 start.sh / start.bat 重启循环约定的退出码：进程以该码退出时由外层脚本重新拉起
RESTART_EXIT_CODE = 42


class Lifecycle:
    """全局单例生命周期管理。"""

    _instances: Dict[str, Any] = {}
    _cleanups: List[tuple[str, CleanupFn]] = []
    _start_hooks: List[tuple[str, HookFn]] = []
    _tick_hooks: List[tuple[str, HookFn]] = []
    _shutdown_requester: Optional[Callable[[], None]] = None
    _restart_requested: bool = False

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
        # 同名注册整体替换条目：无论新注册是否提供回调，旧回调一律丢弃
        cls._cleanups = [(n, fn) for n, fn in cls._cleanups if n != name]
        cls._start_hooks = [(n, fn) for n, fn in cls._start_hooks if n != name]
        cls._tick_hooks = [(n, fn) for n, fn in cls._tick_hooks if n != name]
        if cleanup:
            cls._cleanups.append((name, cleanup))
        if on_start:
            cls._start_hooks.append((name, on_start))
        if on_tick:
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
                log(f"tick 钩子失败: {name} - {e}", "WARNING")

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
        cls._shutdown_requester = None
        cls._restart_requested = False

    @classmethod
    def set_shutdown_requester(cls, fn: Callable[[], None]) -> None:
        """注册进程关闭触发器（由入口脚本注入，供 Web API 等请求优雅关闭/重启）。"""
        cls._shutdown_requester = fn

    @classmethod
    def request_shutdown(cls, restart: bool = False) -> None:
        """请求优雅关闭；restart=True 时标记重启意图，进程清理完毕后以 RESTART_EXIT_CODE 退出。"""
        if restart:
            cls._restart_requested = True
        if cls._shutdown_requester is not None:
            cls._shutdown_requester()
        else:
            log("收到关闭请求，但关闭触发器未注册", "WARNING")

    @classmethod
    def restart_requested(cls) -> bool:
        """是否已标记重启意图。"""
        return cls._restart_requested
