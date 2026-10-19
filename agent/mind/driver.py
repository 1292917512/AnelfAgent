"""主 Mind 单航班调度器：唯一 worker 串行推进消息周期、心跳与具名任务。

架构不变量：主 Mind 同一时刻至多一个顶层思维工作在执行——多频道多用户
共享同一个 PFC 态势与这一个 worker；并行只属于显式隔离执行体（子代理 /
LLM 钩子 / 工作流 ask / Minecraft 自主轮次），它们不经本调度器。

受理语义：request_cycle / submit_tick / submit_task 只登记并唤醒，忙碌
期间的唤醒被锁存、当前工作结束后继续 drain；延迟续轮由统一计时器管理，
被即时唤醒取代时不重复触发。任务在排队前即认领（queued 与 running 共用
任务在飞集合），未开始的认领在关停时释放。

模型可见：周期异常后的未消费待办按新态势重新进入主周期，不注入额外提示。
Token：恢复复用正常周期的模型调用；退避等待不调用模型。
缓存：不改上下文组装或稳定前缀，仅改变已有待办的调度时机。
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from core.log import log

if TYPE_CHECKING:
    from agent.mind.mind import Mind


@dataclass
class _TaskRequest:
    """一条已认领的具名任务：名字、触发源与终态回执。"""

    name: str
    trigger: str
    future: "asyncio.Future[Optional[str]]"


class MindDriver:
    """以常驻 worker 串行执行主 Mind 工作，唤醒请求合并且不丢失。"""

    def __init__(self, mind: "Mind") -> None:
        self._mind = mind
        self._worker: Optional[asyncio.Task[None]] = None
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._cycle_requested = False
        self._heartbeat_requested = False
        self._task_requests: "deque[_TaskRequest]" = deque()
        self._tick_requested = False
        self._tick_waiters: List["asyncio.Future[List[str]]"] = []
        self._delayed_timer: Optional[asyncio.TimerHandle] = None
        self._delayed_due = 0.0
        self._running = False
        self._current_work = ""
        self._closed = False

    # ------------------------------------------------------------------
    # 只读状态
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """是否有主工作正在执行（不含排队与延迟唤醒）。"""
        return self._running

    @property
    def is_busy(self) -> bool:
        """是否有正在执行或已受理待执行的主工作。"""
        return self._running or self._has_admitted_work()

    def _has_admitted_work(self) -> bool:
        """是否已有已受理待执行的主工作（方法形态避免类型收窄误判）。"""
        return self._cycle_requested or self._heartbeat_requested \
            or bool(self._task_requests) or self._tick_requested

    @property
    def current_work(self) -> str:
        """当前主工作标识（cycle / heartbeat / tick / task:<名>），空闲为空。"""
        return self._current_work

    # ------------------------------------------------------------------
    # 受理入口（只登记与唤醒，不执行）
    # ------------------------------------------------------------------

    def request_cycle(self, *, is_heartbeat: bool = False, delay: float = 0.0) -> None:
        """请求一次主周期；重复请求合并，即时请求取代延迟续轮。"""
        if self._closed:
            return
        if is_heartbeat:
            self._heartbeat_requested = True
            self._request_wake()
            return
        if delay > 0 and not self._cycle_requested:
            self._schedule_delayed_cycle(delay)
            return
        self._cancel_delayed_cycle()
        self._cycle_requested = True
        self._request_wake()

    def submit_tick(self) -> "asyncio.Future[List[str]]":
        """提交一次手动心跳；同一在途 tick 的全部等待者共享结果。"""
        future: "asyncio.Future[List[str]]" = asyncio.get_running_loop().create_future()
        if self._closed:
            future.cancel()
            return future
        self._tick_waiters.append(future)
        self._tick_requested = True
        self._request_wake()
        return future

    def submit_task(
        self, task_name: str, *, trigger: str = "manual",
    ) -> "tuple[bool, str, Optional[asyncio.Future[Optional[str]]]]":
        """认领并排入具名任务；返回受理状态、说明与终态回执。"""
        engine = self._mind.heartbeat_engine
        accepted, message = engine.claim_task(task_name)
        if not accepted:
            return False, message, None
        if self._closed:
            engine.release_task_claim(task_name)
            return False, "主思维调度已关闭", None
        future: "asyncio.Future[Optional[str]]" = asyncio.get_running_loop().create_future()
        self._task_requests.append(_TaskRequest(task_name, trigger, future))
        self._request_wake()
        return True, "任务已受理，正在后台执行", future

    async def when_idle(self) -> None:
        """等待当前与已受理的即时工作全部结束（延迟续轮不算活跃工作）。"""
        while self.is_busy:
            await self._idle.wait()

    async def close(self) -> None:
        """停止受理并终止 worker；未开始的任务认领立即释放。"""
        if self._closed:
            return
        self._closed = True
        self._cancel_delayed_cycle()
        self._cycle_requested = False
        self._heartbeat_requested = False
        self._tick_requested = False
        for future in self._tick_waiters:
            if not future.done():
                future.cancel()
        self._tick_waiters.clear()
        while self._task_requests:
            request = self._task_requests.popleft()
            self._mind.heartbeat_engine.release_task_claim(request.name)
            if not request.future.done():
                request.future.cancel()
        self._wake.set()
        worker = self._worker
        if worker is not None and worker is not asyncio.current_task():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._worker = None
        self._running = False
        self._current_work = ""
        self._idle.set()

    # ------------------------------------------------------------------
    # worker
    # ------------------------------------------------------------------

    def _request_wake(self) -> None:
        self._idle.clear()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="agent.mind.driver")
        self._wake.set()

    def _schedule_delayed_cycle(self, delay: float) -> None:
        loop = asyncio.get_running_loop()
        due = loop.time() + delay
        if self._delayed_timer is not None and self._delayed_due <= due:
            return  # 已有更早的延迟续轮在途，不重复排
        self._cancel_delayed_cycle()
        self._delayed_due = due
        self._delayed_timer = loop.call_later(delay, self._fire_delayed_cycle)

    def _cancel_delayed_cycle(self) -> None:
        if self._delayed_timer is not None:
            self._delayed_timer.cancel()
            self._delayed_timer = None
        self._delayed_due = 0.0

    def _fire_delayed_cycle(self) -> None:
        self._delayed_timer = None
        self._delayed_due = 0.0
        if self._closed:
            return
        self._cycle_requested = True
        self._request_wake()

    async def _run(self) -> None:
        try:
            while not self._closed:
                if self.is_busy:
                    self._idle.clear()
                    await self._run_next()
                    continue
                self._idle.set()
                self._wake.clear()
                if self._has_admitted_work():
                    continue  # 登记发生在清位与等待之间：不丢唤醒
                await self._wake.wait()
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            self._current_work = ""
            self._idle.set()

    async def _run_next(self) -> None:
        """取下一件工作并串行执行；取件优先级为用户周期 > 具名任务 > 手动 tick > 心跳。"""
        request: Optional[_TaskRequest] = None
        if self._cycle_requested:
            self._cycle_requested = False
            work = "cycle"
        elif self._task_requests:
            request = self._task_requests.popleft()
            work = f"task:{request.name}"
        elif self._tick_requested:
            work = "tick"
        else:
            self._heartbeat_requested = False
            work = "heartbeat"

        self._running = True
        self._current_work = work
        try:
            if work == "cycle":
                await self._mind._autonomous_cycle(is_heartbeat=False)
            elif request is not None:
                await self._run_task(request)
            elif work == "heartbeat":
                await self._mind.heartbeat_engine.tick()
                await self._mind._autonomous_cycle(is_heartbeat=True)
            else:
                await self._run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"主思维工作异常 [{work}]: {exc}", "WARNING", tag="思维")
            if work in ("cycle", "heartbeat") and (
                self._mind.pfc.has_pending_tasks() or self._mind.pfc.pending_analysis
            ):
                self._mind._schedule_next_cycle("主周期异常后仍有待办", backoff=True)
        finally:
            self._running = False
            self._current_work = ""
            self._mind._reset_phase_if_idle()

    async def _run_tick(self) -> None:
        try:
            executed = await self._mind.heartbeat_engine.tick()
        except asyncio.CancelledError:
            for future in self._tick_waiters:
                if not future.done():
                    future.cancel()
            raise
        except Exception as exc:
            log(f"手动心跳执行异常: {exc}", "WARNING", tag="思维")
            for future in self._tick_waiters:
                if not future.done():
                    future.set_exception(exc)
        else:
            for future in self._tick_waiters:
                if not future.done():
                    future.set_result(executed)
        finally:
            # 直到终态才清批次，执行中提交的等待者也共享本次结果。
            self._tick_waiters.clear()
            self._tick_requested = False

    async def _run_task(self, request: "_TaskRequest") -> None:
        try:
            result = await self._mind.heartbeat_engine.run_claimed_task(
                request.name, trigger=request.trigger,
            )
        except asyncio.CancelledError:
            if not request.future.done():
                request.future.cancel()
            raise
        except Exception as exc:
            # 引擎内部已分级处理执行失败；此处属框架层异常，按无产出收口
            log(f"任务 [{request.name}] 执行异常: {exc}", "ERROR", tag="思维")
            result = None
        if not request.future.done():
            request.future.set_result(result)
