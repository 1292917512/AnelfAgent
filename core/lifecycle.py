"""轻量级单例注册表 — 统一管理全局单例与长驻服务的创建与销毁。

宿主契约：**注册顺序 = 启动顺序，逆序 = 关停顺序**。长驻服务（Web 服务器 /
频道 / 看门狗 / 配置监听等）必须以 on_start + cleanup 成对注册，关停时由
``shutdown_all()`` 统一逆序回收，调用方不得自行管理服务清理顺序。

扩展生命周期钩子：
- ``on_start``：bootstrap 末尾由 ``start_all()`` 正序触发
- ``on_tick``：心跳 tick 时由 ``tick_all()`` 触发
- ``cleanup``（即 on_stop）：shutdown 时由 ``shutdown_all()`` 逆序触发
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from core.config import register_configs_safe
from core.log import log
from core.registration import caller_module, record_mapping, record_undo

CleanupFn = Union[Callable[[], None], Callable[[], Awaitable[None]]]
HookFn = Union[Callable[[], None], Callable[[], Awaitable[None]]]

# 与 start.sh / start.bat 重启循环约定的退出码：进程以该码退出时由外层脚本重新拉起
RESTART_EXIT_CODE = 42


class ShutdownBlockedError(RuntimeError):
    """关键资源未能关闭；调用方不得释放实例所有权或自动强制退出。"""

_LIFECYCLE_CONFIGS = {
    "system/shutdown": {
        "shutdown_budget_seconds": {
            "description": "普通组件关停预算（秒）；关键存储不因预算耗尽而中断或跳过，"
                           "仍等待原生操作和连接关闭",
            "default": 45.0,
            "min": 5.0,
            "max": 300.0,
            "unit": "秒",
            "advanced": True,
        },
    },
}

register_configs_safe(_LIFECYCLE_CONFIGS)


class Lifecycle:
    """全局单例生命周期管理。"""

    _instances: Dict[str, Any] = {}
    _owners: Dict[str, str] = {}
    _cleanups: List[tuple[str, CleanupFn]] = []
    _start_hooks: List[tuple[str, HookFn]] = []
    _tick_hooks: List[tuple[str, HookFn]] = []
    _quiesce_hooks: List[tuple[str, Callable[[], None]]] = []
    _critical_cleanups: Dict[str, bool] = {}
    _quiescing: bool = False
    _order: List[str] = []
    _shutdown_requester: Optional[Callable[[], None]] = None
    _requester_loop: Optional[asyncio.AbstractEventLoop] = None
    _restart_requested: bool = False
    # start_all 是否已完成（运行时后注册的组件据此决定是否立即补启动）
    _started: bool = False

    @classmethod
    def register(
        cls,
        name: str,
        instance: Any,
        cleanup: Optional[CleanupFn] = None,
        on_start: Optional[HookFn] = None,
        on_tick: Optional[HookFn] = None,
        *,
        on_quiesce: Optional[Callable[[], None]] = None,
        critical_cleanup: bool = False,
    ) -> None:
        """注册单例实例，可选附带 cleanup / on_start / on_tick 回调。

        同名重复注册时替换旧实例并丢弃旧回调，避免关闭时重复清理。
        """
        existed = name in cls._instances
        previous = cls._instances.get(name)
        if existed and cls._started and previous is not instance:
            raise RuntimeError(f"组件 {name} 正在运行，必须先停止并注销再替换")
        previous_hooks = {
            attr: [(index, entry) for index, entry in enumerate(getattr(cls, attr)) if entry[0] == name]
            for attr in ("_cleanups", "_start_hooks", "_tick_hooks", "_quiesce_hooks")
        }
        previous_index = cls._order.index(name) if name in cls._order else 0
        def undo() -> None:
            cls._forget(name)
            if existed:
                cls._instances[name] = previous
                cls._order.insert(previous_index, name)
                for attr, entries in previous_hooks.items():
                    for index, entry in entries:
                        getattr(cls, attr).insert(index, entry)
        record_mapping(cls._owners, name)
        record_mapping(cls._critical_cleanups, name)
        record_undo(undo)
        cls._owners[name] = caller_module()
        cls._instances[name] = instance
        # 同名注册整体替换条目：无论新注册是否提供回调，旧回调一律丢弃
        cls._cleanups = [(n, fn) for n, fn in cls._cleanups if n != name]
        cls._start_hooks = [(n, fn) for n, fn in cls._start_hooks if n != name]
        cls._tick_hooks = [(n, fn) for n, fn in cls._tick_hooks if n != name]
        cls._quiesce_hooks = [(n, fn) for n, fn in cls._quiesce_hooks if n != name]
        cls._critical_cleanups.pop(name, None)
        cls._order = [n for n in cls._order if n != name]
        cls._order.append(name)
        if cleanup:
            cls._cleanups.append((name, cleanup))
            if critical_cleanup:
                cls._critical_cleanups[name] = True
        if on_quiesce:
            cls._quiesce_hooks.append((name, on_quiesce))
            if cls._quiescing:
                on_quiesce()
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
        cls._started = True

    @classmethod
    def started(cls) -> bool:
        """start_all 是否已完成（运行时后注册的组件据此决定立即补启动）。"""
        return cls._started

    @classmethod
    async def start_one(cls, name: str) -> bool:
        """单独执行指定组件的 on_start 钩子（运行时后注册组件的补启动）。"""
        fn = next((f for n, f in cls._start_hooks if n == name), None)
        if fn is None:
            return False
        try:
            if asyncio.iscoroutinefunction(fn):
                await fn()
            else:
                fn()
            return True
        except Exception as e:
            log(f"启动钩子失败: {name} - {e}", "WARNING")
            return False

    @classmethod
    async def unregister(cls, name: str, per_timeout: Optional[float] = 30.0) -> bool:
        """注销单个组件：执行其 cleanup 后从注册表摘除（模块热拔除回收用）。

        与 shutdown_all 的全量逆序语义不同，仅影响指定组件；
        cleanup 失败保留注册与回调，允许后续重试。
        """
        known = name in cls._instances or any(n == name for n, _ in cls._cleanups)
        if not known:
            return False
        cleanup = next((fn for n, fn in cls._cleanups if n == name), None)
        if cleanup is not None:
            try:
                quiesce = next((fn for n, fn in cls._quiesce_hooks if n == name), None)
                if quiesce:
                    quiesce()
                if asyncio.iscoroutinefunction(cleanup):
                    if name in cls._critical_cleanups:
                        await cls._await_critical(name, cleanup)
                    elif per_timeout is not None:
                        await asyncio.wait_for(cleanup(), timeout=per_timeout)
                    else:
                        await cleanup()
                else:
                    cleanup()
                log(f"已清理: {name}")
            except asyncio.TimeoutError:
                log(f"清理超时（{per_timeout}s）: {name}", "ERROR")
                return False
            except Exception as e:
                log(f"清理失败: {name} - {e}", "WARNING")
                return False
        cls._forget(name)
        return True

    @classmethod
    def _forget(cls, name: str) -> None:
        """移除组件注册与钩子；调用方负责先完成资源清理。"""
        cls._instances.pop(name, None)
        cls._owners.pop(name, None)
        cls._cleanups = [(n, fn) for n, fn in cls._cleanups if n != name]
        cls._start_hooks = [(n, fn) for n, fn in cls._start_hooks if n != name]
        cls._tick_hooks = [(n, fn) for n, fn in cls._tick_hooks if n != name]
        cls._quiesce_hooks = [(n, fn) for n, fn in cls._quiesce_hooks if n != name]
        cls._critical_cleanups.pop(name, None)
        cls._order = [n for n in cls._order if n != name]

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
    async def shutdown_all(
        cls,
        per_timeout: Optional[float] = 30.0,
        *,
        deadline: Optional[float] = None,
    ) -> None:
        """逆序执行所有 cleanup 回调，释放资源。

        per_timeout / deadline 只约束普通组件，预算耗尽后继续寻找关键清理。
        critical_cleanup 不被超时或外层取消中断；关闭失败阻止后续依赖释放，
        宿主保留实例所有权和进程，等待人工排查。
        """
        cls.quiesce_all()
        for name, fn in reversed(cls._cleanups):
            critical = name in cls._critical_cleanups
            effective = per_timeout
            if deadline is not None and not critical:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log(f"关停预算耗尽，跳过普通组件清理: {name}", "ERROR")
                    continue
                effective = min(per_timeout, remaining) if per_timeout is not None else remaining
            try:
                if asyncio.iscoroutinefunction(fn):
                    if critical:
                        await cls._await_critical(name, fn)
                    elif effective is not None:
                        await asyncio.wait_for(fn(), timeout=effective)
                    else:
                        await fn()
                else:
                    fn()
                log(f"已清理: {name}")
                cls._critical_cleanups.pop(name, None)
            except asyncio.TimeoutError as exc:
                if critical:
                    raise ShutdownBlockedError(f"关键资源关闭超时: {name}") from exc
                log(f"清理超时（{effective:.0f}s）: {name}", "ERROR")
            except Exception as e:
                if critical:
                    raise ShutdownBlockedError(f"关键资源关闭失败: {name} - {e}") from e
                log(f"清理失败: {name} - {e}", "WARNING")
        cls._instances.clear()
        cls._owners.clear()
        cls._cleanups.clear()
        cls._start_hooks.clear()
        cls._tick_hooks.clear()
        cls._quiesce_hooks.clear()
        cls._order.clear()
        cls._started = False

    @classmethod
    def quiesce_all(cls) -> None:
        """关停第一步：同步关闭工作入口，不等待批次完成，不改变持久化配置。"""
        if cls._quiescing:
            return
        cls._quiescing = True
        for name, fn in reversed(cls._quiesce_hooks):
            try:
                fn()
            except Exception as exc:
                raise ShutdownBlockedError(f"停止接纳工作失败: {name} - {exc}") from exc

    @classmethod
    def shutdown_blockers(cls) -> list[str]:
        """尚未确认关闭的关键资源；供线程看门狗判断是否允许强制退出。"""
        return list(cls._critical_cleanups)

    @staticmethod
    async def _await_critical(name: str, fn: CleanupFn) -> None:
        """保持关键关闭任务存活；取消和普通超时不能中断原生资源回收。"""
        result = fn()
        if result is None:
            return
        task = asyncio.ensure_future(result)
        started = time.monotonic()
        while not task.done():
            try:
                await asyncio.wait({task}, timeout=30.0)
            except asyncio.CancelledError:
                log(f"继续等待关键资源关闭，不中断写盘: {name}", "WARNING", tag="关停")
            if not task.done():
                log(f"等待关键资源关闭: {name}，已等待 {time.monotonic() - started:.0f}s",
                    "WARNING", tag="关停")
        if task.cancelled():
            raise ShutdownBlockedError(f"关键资源关闭任务被取消: {name}")
        task.result()

    @classmethod
    def snapshot(cls) -> List[Dict[str, Any]]:
        """注册表快照（名称 / 实例类型 / 钩子配置 / 注册顺序），供状态接口展示。"""
        cleanup_names = {n for n, _ in cls._cleanups}
        start_names = {n for n, _ in cls._start_hooks}
        tick_names = {n for n, _ in cls._tick_hooks}
        return [
            {
                "name": name,
                "module": cls._owners.get(name, ""),
                "order": index + 1,
                "instance_type": type(cls._instances.get(name)).__name__,
                "has_cleanup": name in cleanup_names,
                "has_on_start": name in start_names,
                "has_on_tick": name in tick_names,
            }
            for index, name in enumerate(cls._order)
        ]

    @classmethod
    def reset(cls) -> None:
        """重置所有注册（测试用）。"""
        cls._instances.clear()
        cls._owners.clear()
        cls._cleanups.clear()
        cls._start_hooks.clear()
        cls._tick_hooks.clear()
        cls._quiesce_hooks.clear()
        cls._critical_cleanups.clear()
        cls._quiescing = False
        cls._order.clear()
        cls._shutdown_requester = None
        cls._requester_loop = None
        cls._restart_requested = False
        cls._started = False

    @classmethod
    def set_shutdown_requester(cls, fn: Callable[[], None]) -> None:
        """注册进程关闭触发器（由入口脚本注入，供 Web API 等请求优雅关闭/重启）。

        须在主事件循环线程内调用：捕获所属循环，使 request_shutdown 可跨线程安全触发。
        """
        cls._shutdown_requester = fn
        try:
            cls._requester_loop = asyncio.get_running_loop()
        except RuntimeError:
            cls._requester_loop = None

    @classmethod
    def request_shutdown(cls, restart: bool = False) -> None:
        """请求优雅关闭；restart=True 时标记重启意图，进程清理完毕后以 RESTART_EXIT_CODE 退出。

        线程安全：任意线程（如工具工作线程）均可调用，
        经捕获的主循环 call_soon_threadsafe 派发到循环线程执行。
        """
        requester = cls._shutdown_requester
        if requester is None:
            log("收到关闭请求，但关闭触发器未注册", "WARNING")
            return
        # 仅在关闭能实际触发后才记录重启意图，避免残留旗标导致日后正常退出被误判为重启
        if restart:
            cls._restart_requested = True
        loop = cls._requester_loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(requester)
        else:
            requester()

    @classmethod
    def restart_requested(cls) -> bool:
        """是否已标记重启意图。"""
        return cls._restart_requested
