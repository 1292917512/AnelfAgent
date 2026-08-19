"""应用宿主 — 进程级生命周期的唯一编排者。

三段式运行（prepare / run / complete）：

1. 启动：执行 startup FlowMachine（一次性初始化步骤），随后
   ``Lifecycle.start_all()`` 正序拉起所有注册服务的 on_start 钩子
2. 运行：阻塞等待关停事件（OS 信号 / ``Lifecycle.request_shutdown``）
3. 关停：前置钩子（记忆兜底 / 日志静音 / 后台任务取消）→
   ``Lifecycle.shutdown_all()`` 逆序回收全部服务

core 不依赖 agent：关停前置钩子由组合根（launch.py）注入。
"""
import asyncio
import contextlib
import signal
from typing import Any, Dict, List, Optional, Tuple

from core.flow import FlowMachine, FlowResult
from core.lifecycle import HookFn, Lifecycle
from core.log import log


class Application:
    """进程宿主：启动流程 → 等待关停信号 → 逆序关停。"""

    def __init__(self) -> None:
        self.startup = FlowMachine()
        self.last_startup: Optional[FlowResult] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._pre_shutdown_hooks: List[Tuple[str, HookFn]] = []

    def on_pre_shutdown(self, name: str, fn: HookFn) -> None:
        """注册关停前置钩子（在 Lifecycle.shutdown_all 之前按注册顺序执行）。"""
        self._pre_shutdown_hooks.append((name, fn))

    async def run(self) -> None:
        """编排进程生命周期；启动失败也会走完关停序列以回收半成品资源。"""
        self._shutdown_event = asyncio.Event()
        self._arm_signals(asyncio.get_running_loop())
        Lifecycle.register("application", self)

        self.last_startup = await self.startup.execute()
        if self.last_startup.success:
            await Lifecycle.start_all()
            # 启动期间第三方库可能抢走 SIGINT/SIGTERM 处理器，进入运行期前重新布防
            self._arm_signals(asyncio.get_running_loop())
            await self._shutdown_event.wait()
        else:
            log("启动流程未完成，直接进入关停", "ERROR", tag="启动")

        await self._shutdown()

    async def _shutdown(self) -> None:
        """关停序列：前置钩子（失败降级为日志）→ Lifecycle 逆序清理。"""
        log("正在关闭...")
        for name, fn in self._pre_shutdown_hooks:
            try:
                result: Any = fn()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                log(f"关停前置钩子失败: {name} - {exc}", "WARNING", tag="关停")
        await Lifecycle.shutdown_all()

    def _arm_signals(self, loop: asyncio.AbstractEventLoop) -> None:
        """布防 SIGINT/SIGTERM：首次触发优雅关停，二次触发移除处理器走默认强杀。"""
        assert self._shutdown_event is not None
        event = self._shutdown_event

        def _request_shutdown() -> None:
            if not event.is_set():
                event.set()

        # 供 Web API 等运行期入口触发优雅关闭/重启（Lifecycle.request_shutdown）
        Lifecycle.set_shutdown_requester(_request_shutdown)

        def _on_signal() -> None:
            if event.is_set():
                for s in (signal.SIGINT, signal.SIGTERM):
                    with contextlib.suppress(NotImplementedError, ValueError, RuntimeError):
                        loop.remove_signal_handler(s)
                return
            _request_shutdown()

        # Windows ProactorEventLoop 不支持 add_signal_handler
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            def _win_handler(signum: int, frame: object) -> None:
                loop.call_soon_threadsafe(_request_shutdown)

            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(sig, _win_handler)

    def startup_timeline(self) -> List[Dict[str, Any]]:
        """最近一次启动流程的节点时间线（名称 / 状态 / 耗时 / 尝试次数），供状态接口展示。"""
        if self.last_startup is None:
            return []
        return [
            {
                "name": r.name,
                "state": r.state.value,
                "duration": round(r.duration, 3),
                "attempts": r.attempts,
                "error": str(r.error) if r.error else None,
            }
            for r in self.last_startup.results
        ]
