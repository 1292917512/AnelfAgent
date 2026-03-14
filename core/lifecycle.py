"""轻量级单例注册表 — 统一管理全局单例的创建与销毁。

启动时通过 ``Lifecycle.register()`` 注册组件及其 cleanup 回调，
关闭时通过 ``Lifecycle.shutdown_all()`` 逆序执行所有回调，确保资源正确释放。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from core.log import log

CleanupFn = Union[Callable[[], None], Callable[[], Awaitable[None]]]


class Lifecycle:
    """全局单例生命周期管理。"""

    _instances: Dict[str, Any] = {}
    _cleanups: List[tuple[str, CleanupFn]] = []

    @classmethod
    def register(cls, name: str, instance: Any, cleanup: Optional[CleanupFn] = None) -> None:
        """注册单例实例，可选附带 cleanup 回调。"""
        cls._instances[name] = instance
        if cleanup:
            cls._cleanups.append((name, cleanup))

    @classmethod
    def get(cls, name: str) -> Optional[Any]:
        """按名称获取已注册实例。"""
        return cls._instances.get(name)

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

    @classmethod
    def reset(cls) -> None:
        """重置所有注册（测试用）。"""
        cls._instances.clear()
        cls._cleanups.clear()
