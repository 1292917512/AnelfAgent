from __future__ import annotations

import asyncio
from typing import List, Optional

from agent.messages import Everything
from agent.mind.mind import Mind
from core.log import log

DEFAULT_HEARTBEAT_INTERVAL = 300.0


class AgentAssistant:
    """智能体执行壳：消息到达即感知入窗，批量驱动 Mind 统一决策。

    消息到达时立即写入对话历史（保证时序）并入 PFC 队列，
    Mind 空闲后一次性排空唤醒队列（自然 CD），
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
        """消息到达入口：立即感知（写入对话历史 + 入 PFC 队列），再入队等待统一决策。

        对话历史是与 AI 共同维护的消息窗口——无论 AI 是否正在思考，
        新消息都必须第一时间按到达时序插入，保证每轮 LLM 看到严格时序的上下文。
        """
        self._ensure_started()
        try:
            await self.mind.accept_feel(anything)
        except Exception as exc:
            # 感知写入失败的消息不再入队：accept_feel 未生效意味着该消息
            # 未进入对话历史/PFC，入队只会让 Mind 基于缺失上下文决策；
            # 丢弃并以 ERROR 日志告警（含异常详情），由上游频道重试语义兜底
            log(f"消息感知处理失败: {exc}", "ERROR", tag="运行时")
            return
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
                    pass  # 取消属正常关闭流程（正常控制流，非异常）
        self._task = None
        self._heartbeat_task = None

    async def _run_loop(self) -> None:
        """批量消息处理循环。

        阻塞等待首条消息 → 排空队列中所有已到达的消息 → 一次 execute_mind 统一决策。
        消息在 feel() 到达时已写入对话历史并入 PFC 队列，此处仅负责触发决策；
        Mind 执行期间新到的消息自然积累（CD），由下一轮或循环内合并机制处理。
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
                if self.mind.is_reflecting:
                    await self._notify_heartbeat_busy(batch)
                await self.mind.execute_mind()
                await self._drain_pending_tasks()
            except Exception:
                log("AgentAssistant 批量处理异常", "ERROR", tag="运行时")
                # 异常后 PFC 中仍有待处理项时主动调度下一轮：
                # 否则消息要等当前长任务/下次心跳收尾才有机会被重新触发
                if self.mind.pfc.has_pending_tasks():
                    self.mind._schedule_next_cycle("批量处理异常后仍有待处理任务")
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
                if self.mind.pfc.has_pending_tasks():
                    self.mind._schedule_next_cycle("PFC 自排空异常后仍有待处理任务")

    def _current_heartbeat_interval(self) -> float:
        """动态获取心跳间隔，支持运行时热更新。"""
        try:
            from agent.config import get_config_provider
            return get_config_provider().mind.heartbeat_interval
        except Exception:
            return self._heartbeat_interval

    def _busy_defer_seconds(self) -> float:
        """心跳忙碌延后轮询间隔（配置 heartbeat_busy_defer_seconds，热读取）。"""
        try:
            from agent.config import get_config_provider
            return max(5.0, get_config_provider().mind.heartbeat_busy_defer_seconds)
        except Exception:
            return 60.0

    def _mind_busy(self) -> bool:
        """Mind 是否正在执行中（回复/反思/上一轮心跳 tick 未收尾）。

        忙碌时心跳不整轮跳过，而是按 _busy_defer_seconds 短间隔轮询，
        空闲后立即补跑——被延后的 tick 不递增任何计数器。
        """
        m = self.mind
        return bool(m.is_reply or m.is_reflecting or m._heartbeat_running)

    async def _heartbeat_loop(self) -> None:
        """定期触发 Mind 自主思考（反思、主动行为、目标推进等）。

        AI 执行中（非空闲）时延后心跳而非跳过整轮：短间隔轮询忙碌状态，
        空闲后立即补跑，保证空闲思考（idle 调度）与维护不被长对话饿死。
        """
        while True:
            await asyncio.sleep(self._current_heartbeat_interval())
            while self._mind_busy():
                await asyncio.sleep(self._busy_defer_seconds())
            try:
                await self.mind.execute_mind(is_heartbeat=True)
            except Exception:
                log("心跳自主思考异常", "ERROR", tag="运行时")
