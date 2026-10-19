"""AgentAssistant 的消息受理、心跳热更与关停契约。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agent.runtime.assistant import AgentAssistant


class TestFeelAdmission:
    @staticmethod
    def _fake_mind(calls: list[str]) -> Any:
        return SimpleNamespace(
            accept_feel=AsyncMock(side_effect=lambda _anything: calls.append("accept")),
            driver=SimpleNamespace(
                is_running=False, current_work="", request_cycle=lambda: calls.append("wake"),
                close=AsyncMock(),
            ),
        )

    async def test_feel_persists_before_driver_wake(self) -> None:
        calls: list[str] = []
        mind = self._fake_mind(calls)
        assistant = AgentAssistant(mind, heartbeat_enabled=False)
        await assistant.feel(object())
        assert calls == ["accept", "wake"]

    async def test_failed_perception_does_not_wake_driver(self) -> None:
        mind = SimpleNamespace(
            accept_feel=AsyncMock(side_effect=RuntimeError("write failed")),
            driver=SimpleNamespace(
                is_running=False, current_work="", request_cycle=Mock(),
                close=AsyncMock(),
            ),
        )
        assistant = AgentAssistant(mind, heartbeat_enabled=False)
        with pytest.raises(RuntimeError, match="write failed"):
            await assistant.feel(object())
        assert mind.accept_feel.await_count == 2
        mind.driver.request_cycle.assert_not_called()


class TestBusyHint:
    """消息将排在自主工作之后时，每忙碌周期向来源会话提示一次。"""

    @staticmethod
    def _msg(scope: str) -> Any:
        return SimpleNamespace(
            entity_scope=scope,
            adapter_key="qq",
            trigger_mind=True,
            images=None,
            media_segments=None,
            get_text_content=lambda: "在吗",
        )

    @staticmethod
    def _mind(work: str) -> Any:
        return SimpleNamespace(
            accept_feel=AsyncMock(),
            _active_scopes=set(),
            _reflecting=False,
            channel_manager=SimpleNamespace(reply=AsyncMock()),
            driver=SimpleNamespace(
                is_running=bool(work), current_work=work,
                request_cycle=Mock(), close=AsyncMock(),
            ),
        )

    async def test_hints_once_per_scope_behind_autonomous_work(self) -> None:
        mind = self._mind("task:a")
        assistant = AgentAssistant(mind, heartbeat_enabled=False)
        await assistant.feel(self._msg("user_qq:1"))
        await assistant.feel(self._msg("user_qq:1"))
        await asyncio.gather(*assistant._busy_hint_tasks.values())
        assert mind.channel_manager.reply.await_count == 1
        mind.driver.request_cycle.assert_called()

    async def test_no_hint_for_cycle_work_or_merging_scope(self) -> None:
        mind = self._mind("cycle")
        assistant = AgentAssistant(mind, heartbeat_enabled=False)
        await assistant.feel(self._msg("user_qq:1"))
        mind.channel_manager.reply.assert_not_called()

        merging = self._mind("heartbeat")
        merging._active_scopes.add("user_qq:1")
        assistant2 = AgentAssistant(merging, heartbeat_enabled=False)
        await assistant2.feel(self._msg("user_qq:1"))
        merging.channel_manager.reply.assert_not_called()

    async def test_new_busy_episode_hints_again(self) -> None:
        mind = self._mind("tick")
        assistant = AgentAssistant(mind, heartbeat_enabled=False)
        await assistant.feel(self._msg("user_qq:1"))
        await asyncio.gather(*assistant._busy_hint_tasks.values())
        assert mind.channel_manager.reply.await_count == 1
        mind.driver.is_running = False
        mind.driver.current_work = ""
        await assistant.feel(self._msg("user_qq:1"))
        assert mind.channel_manager.reply.await_count == 1  # 空闲段不提示并清台账
        mind.driver.is_running = True
        mind.driver.current_work = "heartbeat"
        await assistant.feel(self._msg("user_qq:1"))
        await asyncio.gather(*assistant._busy_hint_tasks.values())
        assert mind.channel_manager.reply.await_count == 2  # 新忙碌周期再提示一次

    @pytest.mark.parametrize("finish", ["stop", "timeout", "error"])
    async def test_slow_hint_does_not_block_ingress_and_is_reaped(self, monkeypatch, finish: str) -> None:
        mind = self._mind("heartbeat")
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(*args):
            entered.set()
            await release.wait()
            if finish == "error":
                raise OSError("channel unavailable")

        mind.channel_manager.reply.side_effect = send
        if finish == "timeout":
            monkeypatch.setattr("agent.runtime.assistant._BUSY_HINT_TIMEOUT", 0.02)
        assistant = AgentAssistant(mind, heartbeat_enabled=False)
        try:
            await asyncio.wait_for(assistant.feel(self._msg("user_qq:1")), 1)
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(assistant.feel(self._msg("user_qq:2")), 1)
            assert mind.accept_feel.await_count == 2
            assert mind.driver.request_cycle.call_count == 2
            if finish == "stop":
                await asyncio.wait_for(assistant.stop(), 1)
            else:
                if finish == "error":
                    release.set()
                await asyncio.wait_for(asyncio.gather(*assistant._busy_hint_tasks.values()), 1)
                before = mind.channel_manager.reply.await_count
                await assistant.feel(self._msg("user_qq:1"))
                assert mind.channel_manager.reply.await_count == before
            assert not assistant._busy_hint_tasks
        finally:
            await assistant.stop()


class TestHeartbeatIntervalHotReload:
    """间隔配置热更：ConfigManager 监听在真实变更时唤醒进行中的 sleep 重排。"""

    @staticmethod
    def _fake_mind() -> Any:
        return SimpleNamespace(
            driver=SimpleNamespace(is_busy=False, close=AsyncMock()),
            execute_mind=AsyncMock(),
        )

    async def test_same_value_sync_does_not_wake(self) -> None:
        """同值写入不得重置 sleep 进度。"""
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
        await asyncio.sleep(0)
        assert assistant._interval_wake.is_set()

    async def test_loop_applies_new_interval_after_wake(self, monkeypatch) -> None:
        """长 sleep 被配置变更唤醒，立即按新短间隔触发心跳。"""
        mind = self._fake_mind()
        assistant = AgentAssistant(mind, heartbeat_interval=3600.0)
        holder = [3600.0]
        monkeypatch.setattr(assistant, "_current_heartbeat_interval", lambda: holder[0])
        task = asyncio.create_task(assistant._heartbeat_loop())
        try:
            await asyncio.sleep(0.05)
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
        await asyncio.sleep(0.02)
        assert assistant._on_interval_config_changed in ConfigManager._listeners.get(
            "heartbeat_interval", [])
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert assistant._on_interval_config_changed not in ConfigManager._listeners.get(
            "heartbeat_interval", [])
