"""AgentAssistant 异常恢复：周期异常后 PFC 仍有待处理项时主动调度下一轮。

回归背景：_run_loop 对 execute_mind 异常只记日志不重试，长任务执行期间
消息触发的周期一旦异常，用户消息要等任务收尾或下次心跳才被处理。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock

from agent.runtime.assistant import AgentAssistant


def _fake_mind(*, has_pending: bool, scheduled: List[str]) -> Any:
    return SimpleNamespace(
        is_reflecting=False,
        execute_mind=AsyncMock(side_effect=RuntimeError("boom")),
        pfc=SimpleNamespace(has_pending_tasks=lambda: has_pending),
        _schedule_next_cycle=lambda reason: scheduled.append(reason),
    )


async def _run_one_batch(assistant: AgentAssistant, scheduled: List[str]) -> None:
    await assistant._queue.put(object())
    task = asyncio.create_task(assistant._run_loop())
    try:
        # 等待首批消息处理完毕（队列 task_done 归零）或调度已触发
        for _ in range(50):
            await asyncio.sleep(0)
            if scheduled:
                break
        await asyncio.wait_for(assistant._queue.join(), timeout=1.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestRunLoopExceptionRecovery:
    async def test_reschedules_when_pending_tasks_remain(self) -> None:
        scheduled: List[str] = []
        assistant = AgentAssistant(_fake_mind(has_pending=True, scheduled=scheduled), heartbeat_enabled=False)
        await _run_one_batch(assistant, scheduled)
        assert len(scheduled) == 1

    async def test_no_reschedule_when_nothing_pending(self) -> None:
        scheduled: List[str] = []
        assistant = AgentAssistant(_fake_mind(has_pending=False, scheduled=scheduled), heartbeat_enabled=False)
        await _run_one_batch(assistant, scheduled)
        assert scheduled == []


class TestHeartbeatIntervalHotReload:
    """间隔配置热更：ConfigManager 监听在真实变更时唤醒进行中的 sleep 重排。

    回归背景（2026-09-17）：旧实现 sleep(旧间隔) 不可中断，间隔改小后
    最长要等一个完整旧周期（可达数小时）才生效，被误判为"配置没有热更新"。
    """

    @staticmethod
    def _fake_mind() -> Any:
        return SimpleNamespace(
            is_reply=False, is_reflecting=False, _heartbeat_running=False,
            execute_mind=AsyncMock(),
        )

    async def test_same_value_sync_does_not_wake(self) -> None:
        """save_mind_config 双轨同步会对全部字段 set，同值写入不得重置 sleep 进度。"""
        assistant = AgentAssistant(self._fake_mind(), heartbeat_interval=300.0)
        assistant._loop = asyncio.get_running_loop()
        assistant._applied_interval = 300.0
        assistant._on_interval_config_changed("heartbeat_interval", 300.0)
        await asyncio.sleep(0)
        assert not assistant._interval_wake.is_set()

    async def test_real_change_wakes(self) -> None:
        assistant = AgentAssistant(self._fake_mind(), heartbeat_interval=300.0)
        assistant._loop = asyncio.get_running_loop()
        assistant._applied_interval = 300.0
        assistant._on_interval_config_changed("heartbeat_interval", 600.0)
        await asyncio.sleep(0)  # 让 call_soon_threadsafe 回调执行
        assert assistant._interval_wake.is_set()

    async def test_loop_applies_new_interval_after_wake(self, monkeypatch) -> None:
        """长 sleep 被配置变更唤醒，立即按新短间隔触发心跳（不等旧周期到期）。"""
        mind = self._fake_mind()
        assistant = AgentAssistant(mind, heartbeat_interval=3600.0)
        holder = [3600.0]
        monkeypatch.setattr(assistant, "_current_heartbeat_interval", lambda: holder[0])
        task = asyncio.create_task(assistant._heartbeat_loop())
        try:
            await asyncio.sleep(0.05)  # 循环已进入 3600s 等待
            assert mind.execute_mind.await_count == 0
            holder[0] = 0.02
            assistant._on_interval_config_changed("heartbeat_interval", 0.02)
            for _ in range(200):
                await asyncio.sleep(0.01)
                if mind.execute_mind.await_count > 0:
                    break
            assert mind.execute_mind.await_count > 0
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_listener_removed_on_stop(self) -> None:
        """循环退出时注销监听，防重启后监听器泄漏重复唤醒。"""
        from core.config import ConfigManager

        assistant = AgentAssistant(self._fake_mind(), heartbeat_interval=3600.0)
        task = asyncio.create_task(assistant._heartbeat_loop())
        await asyncio.sleep(0.02)  # 等监听注册完成
        assert assistant._on_interval_config_changed in ConfigManager._listeners.get(
            "heartbeat_interval", [])
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert assistant._on_interval_config_changed not in ConfigManager._listeners.get(
            "heartbeat_interval", [])
