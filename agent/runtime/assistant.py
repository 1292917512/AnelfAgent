"""消息与心跳的运行时外壳：感知即入库，执行统一交给主思维 Driver。

对话历史与 PFC 入队不等待 Driver（到达即写，保时序）；feel 只负责感知、
必要的忙碌提示与一次唤醒登记。心跳是 Driver 的另一种周期来源，主工作
繁忙时延后而非并行。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from agent.messages import Everything
from agent.mind.mind import Mind
from agent.mind.perception import should_enqueue_external_message
from core.log import log

DEFAULT_HEARTBEAT_INTERVAL = 300.0
_BUSY_HINT = "稍等，我正在自主思考中~"
_BUSY_HINT_TIMEOUT = 10.0


class AgentAssistant:
    """智能体执行壳：消息到达即感知入窗，并唤醒唯一主思维 Driver。

    对话历史与 PFC 入队不等待 Driver；心跳只是另一种周期唤醒来源。
    """

    def __init__(
        self,
        mind: Mind,
        *,
        heartbeat_interval: Optional[float] = None,
        heartbeat_enabled: bool = True,
    ) -> None:
        self.mind = mind
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._heartbeat_interval = heartbeat_interval or self._load_heartbeat_interval()
        self._heartbeat_enabled = heartbeat_enabled
        # 间隔热更：配置变更监听唤醒进行中的 sleep，立即按新间隔重排
        self._interval_wake = asyncio.Event()
        self._applied_interval = self._heartbeat_interval
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # 同一忙碌周期内已提示过的 scope（每 scope 一次，空闲即清）
        self._busy_hinted: set[str] = set()
        self._busy_hint_tasks: dict[str, asyncio.Task[None]] = {}

    @staticmethod
    def _load_heartbeat_interval() -> float:
        try:
            from agent.config import get_config_provider
            return get_config_provider().mind.heartbeat_interval
        except Exception as e:
            log(f"心跳间隔配置加载失败，使用默认值: {e}", "DEBUG")
            return DEFAULT_HEARTBEAT_INTERVAL

    def start(self) -> None:
        """在当前事件循环中启动心跳；主 Driver 由首次工作请求懒启动。"""
        self._ensure_started()

    async def feel(self, anything: Everything) -> None:
        """消息到达入口：立即感知写入，再锁存一次主周期唤醒。"""
        self._ensure_started()
        for attempt in (0, 1):
            try:
                await self.mind.accept_feel(anything)
                break
            except Exception as exc:
                if attempt == 0:
                    # 瞬时故障（SQLite 写竞争等）退避重试一次；上游频道均无重试，
                    # 直接丢弃就是消息丢失
                    log(f"消息感知失败，退避重试: {exc}", "WARNING", tag="运行时")
                    await asyncio.sleep(0.5)
                    continue
                # 感知写入失败的消息不再唤醒：accept_feel 未生效意味着该消息
                # 未进入对话历史/PFC，唤醒只会让 Mind 基于缺失上下文决策
                log(f"消息感知处理失败（重试后仍失败）: {exc}", "ERROR", tag="运行时")
                raise
        self.mind.driver.request_cycle()
        self._hint_if_waiting_behind_work(anything)

    def _hint_if_waiting_behind_work(self, anything: Everything) -> None:
        """消息将排在自主工作之后时，向来源会话发一次简短提示。

        同 scope 在途回复内的新消息经轮内合并处理（不等待、不提示）；
        用户周期之间的正常排队属对话节奏，同样不提示。
        """
        driver = self.mind.driver
        if not driver.is_running or driver.current_work == "cycle":
            self._busy_hinted.clear()
            return
        if not anything.adapter_key or anything.entity_scope in self.mind._active_scopes:
            return
        if not should_enqueue_external_message(anything, reflecting=self.mind._reflecting):
            return
        scope = anything.entity_scope
        if scope in self._busy_hinted or scope in self._busy_hint_tasks:
            return
        self._busy_hinted.add(scope)
        task = asyncio.create_task(self._send_busy_hint(anything), name=f"busy_hint:{scope}")
        self._busy_hint_tasks[scope] = task
        task.add_done_callback(lambda _task: self._busy_hint_tasks.pop(scope, None))

    async def _send_busy_hint(self, anything: Everything) -> None:
        """固定提示只做有界 I/O，不占主思维；失败记录后结束，不重试。"""
        scope = anything.entity_scope
        driver = self.mind.driver
        if not driver.is_running or driver.current_work == "cycle" or scope in self.mind._active_scopes:
            return
        try:
            async with asyncio.timeout(_BUSY_HINT_TIMEOUT):
                await self.mind.channel_manager.reply(anything, _BUSY_HINT)
            log(f"忙碌提示发送调用完成: {scope}", "DEBUG", tag="运行时")
        except Exception as exc:
            log(f"忙碌提示发送失败（不重试）: {scope}: {type(exc).__name__}: {exc}", "WARNING", tag="运行时")

    def _ensure_started(self) -> None:
        if self._heartbeat_enabled and (not self._heartbeat_task or self._heartbeat_task.done()):
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="agent.agent_core.Heartbeat",
            )
            log(f"AgentAssistant 已启动（心跳间隔 {self._heartbeat_interval}s）", tag="运行时")

    async def stop(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass  # 取消属正常关闭流程（正常控制流，非异常）
        self._heartbeat_task = None
        hint_tasks = list(self._busy_hint_tasks.values())
        for task in hint_tasks:
            task.cancel()
        await asyncio.gather(*hint_tasks, return_exceptions=True)
        self._busy_hint_tasks.clear()
        self._busy_hinted.clear()
        await self.mind.driver.close()

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
        """主 Driver 是否已有正在执行或待执行的即时工作。"""
        return self.mind.driver.is_busy

    def _on_interval_config_changed(self, key: str, value: Any) -> None:
        """ConfigManager 变更监听：间隔值真实变化时唤醒进行中的 sleep。"""
        try:
            new_interval = float(value)
        except (TypeError, ValueError):
            return
        if abs(new_interval - self._applied_interval) < 1e-6:
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._interval_wake.set)

    async def _heartbeat_loop(self) -> None:
        """定期请求主 Driver 执行心跳；主工作繁忙时延后而非并行。"""
        from core.config import ConfigManager

        self._loop = asyncio.get_running_loop()
        ConfigManager.add_listener("heartbeat_interval", self._on_interval_config_changed)
        try:
            while True:
                self._interval_wake.clear()
                interval = self._current_heartbeat_interval()
                self._applied_interval = interval
                try:
                    await asyncio.wait_for(self._interval_wake.wait(), timeout=interval)
                    continue  # 间隔配置已变更：立即按新值重算休眠
                except asyncio.TimeoutError:
                    pass
                while self._mind_busy():
                    await asyncio.sleep(self._busy_defer_seconds())
                try:
                    await self.mind.execute_mind(is_heartbeat=True)
                except Exception:
                    log("心跳自主思考异常", "ERROR", tag="运行时")
        finally:
            ConfigManager.remove_listener("heartbeat_interval", self._on_interval_config_changed)
