from __future__ import annotations

import asyncio
from typing import List, Optional

from agent.messages import Everything
from agent.mind.mind import Mind
from core.log import log

DEFAULT_HEARTBEAT_INTERVAL = 300.0


class AgentAssistant:
    """智能体执行壳：批量接收消息，驱动 Mind 统一决策。

    消息到达时入队，Mind 空闲后一次性排空队列（自然 CD），
    心跳定期触发自主思考（反思、主动行为等）。
    """

    def __init__(
        self,
        mind: Mind,
        *,
        heartbeat_interval: Optional[float] = None,
        heartbeat_enabled: bool = True,
    ) -> None:
        self.mind = mind
        self._queue: "asyncio.Queue[Everything]" = asyncio.Queue()
        self._task: Optional[asyncio.Task[None]] = None
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._heartbeat_interval = heartbeat_interval or self._load_heartbeat_interval()
        self._heartbeat_enabled = heartbeat_enabled

    @staticmethod
    def _load_heartbeat_interval() -> float:
        try:
            from agent.config import get_config_provider
            return get_config_provider().mind.heartbeat_interval
        except Exception as e:
            log(f"心跳间隔配置加载失败，使用默认值: {e}", "DEBUG")
            return DEFAULT_HEARTBEAT_INTERVAL

    def start(self) -> None:
        """启动心跳循环（在当前事件循环中）。消息处理循环在 feel() 首次调用时懒启动。"""
        if self._heartbeat_enabled and (not self._heartbeat_task or self._heartbeat_task.done()):
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="agent.agent_core.Heartbeat",
            )
            log(f"心跳已启动（间隔 {self._heartbeat_interval}s）", tag="运行时")

    async def feel(self, anything: Everything) -> None:
        self._ensure_started()
        await self._queue.put(anything)

    def _ensure_started(self) -> None:
        if self._task and not self._task.done():
            if self._heartbeat_enabled and (not self._heartbeat_task or self._heartbeat_task.done()):
                self._heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(), name="agent.agent_core.Heartbeat",
                )
            return
        self._task = asyncio.create_task(self._run_loop(), name="agent.agent_core.AgentAssistant")
        if self._heartbeat_enabled and (not self._heartbeat_task or self._heartbeat_task.done()):
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="agent.agent_core.Heartbeat",
            )
        log(f"AgentAssistant 已启动（心跳间隔 {self._heartbeat_interval}s）", tag="运行时")

    async def stop(self) -> None:
        for task in (self._task, self._heartbeat_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task = None
        self._heartbeat_task = None

    async def _run_loop(self) -> None:
        """批量消息处理循环。

        阻塞等待首条消息 → 排空队列中所有已到达的消息 → 全部 accept_feel →
        一次 execute_mind 统一决策。Mind 执行期间新到的消息自然积累（CD）。
        处理完成后自检 PFC，有待处理任务则短暂延迟后再执行。
        """
        while True:
            first = await self._queue.get()
            batch: List[Everything] = [first]
            while not self._queue.empty():
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            try:
                log(f"批量处理 {len(batch)} 条消息", "DEBUG", tag="运行时")
                for anything in batch:
                    await self.mind.accept_feel(anything)
                if self.mind.is_reflecting:
                    await self._notify_heartbeat_busy(batch)
                await self.mind.execute_mind()
                await self._drain_pending_tasks()
            except Exception:
                log("AgentAssistant 批量处理异常", "ERROR", tag="运行时")
            finally:
                for _ in batch:
                    self._queue.task_done()

    async def _notify_heartbeat_busy(self, batch: List[Everything]) -> None:
        """心跳进行中收到消息，向来源频道发送简短提示。"""
        notified_scopes: set[str] = set()
        for anything in batch:
            if not self.mind.should_enqueue_external_message(anything):
                continue
            if not anything.adapter_key:
                continue
            scope = anything.entity_scope
            if scope in notified_scopes:
                continue
            notified_scopes.add(scope)
            try:
                await self.mind.channel_manager.reply(anything, "稍等，我正在自主思考中~")
                log(f"心跳忙碌提示已发送: {scope}", "DEBUG", tag="运行时")
            except Exception:
                log(f"心跳忙碌提示发送失败: {scope}", "DEBUG", tag="运行时")

    async def _drain_pending_tasks(self) -> None:
        """自检 PFC 非消息任务（画像分析、通用任务），消息任务不重复处理。"""
        if self.mind.is_reply or not self._queue.empty():
            return
        if not self.mind.pfc.pending_analysis or self.mind.pfc.pending_analysis.is_empty():
            if not self.mind.pfc.peek_general_tasks():
                return
        await asyncio.sleep(1.0)
        if not self.mind.is_reply and self._queue.empty():
            try:
                await self.mind.execute_mind()
            except Exception:
                log("PFC 任务自排空异常", "ERROR", tag="运行时")

    def _current_heartbeat_interval(self) -> float:
        """动态获取心跳间隔，支持运行时热更新。"""
        try:
            from agent.config import get_config_provider
            return get_config_provider().mind.heartbeat_interval
        except Exception:
            return self._heartbeat_interval

    async def _heartbeat_loop(self) -> None:
        """定期触发 Mind 自主思考（反思、主动行为、目标推进等）。"""
        while True:
            await asyncio.sleep(self._current_heartbeat_interval())
            if self.mind.is_reply:
                continue
            try:
                await self.mind.execute_mind(is_heartbeat=True)
            except Exception:
                log("心跳自主思考异常", "ERROR", tag="运行时")
