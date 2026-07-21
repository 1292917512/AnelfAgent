"""频道看门狗（agent.channel.supervision）单元测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Dict
from unittest.mock import AsyncMock

import pytest

from agent.channel.channel_types import ChannelStatus
from agent.channel.supervision import ChannelSupervisor


def _channel(status: ChannelStatus) -> SimpleNamespace:
    return SimpleNamespace(_status=status, channel_id="test", display_name="测试频道")


class _FakeManager:
    def __init__(self, channels: Dict[str, SimpleNamespace]) -> None:
        self._channels = channels
        self.start_channel = AsyncMock(return_value=True)

    def list_channels(self) -> Dict[str, SimpleNamespace]:
        return dict(self._channels)


class TestInspectOnce:
    async def test_error_channel_restarted(self) -> None:
        ch = _channel(ChannelStatus.ERROR)
        mgr = _FakeManager({"qq": ch})
        sup = ChannelSupervisor(mgr, interval=999, base_backoff=0)
        await sup._inspect_once()
        mgr.start_channel.assert_awaited_once_with("qq")

    async def test_running_channel_resets_fail_count(self) -> None:
        ch = _channel(ChannelStatus.RUNNING)
        mgr = _FakeManager({"qq": ch})
        sup = ChannelSupervisor(mgr, interval=999)
        sup._fail_counts["qq"] = 3
        await sup._inspect_once()
        assert "qq" not in sup._fail_counts
        mgr.start_channel.assert_not_awaited()

    async def test_stopped_channel_not_touched(self) -> None:
        """主动停止的频道绝不重启（用户意图优先）。"""
        ch = _channel(ChannelStatus.STOPPED)
        mgr = _FakeManager({"qq": ch})
        sup = ChannelSupervisor(mgr, interval=999)
        await sup._inspect_once()
        mgr.start_channel.assert_not_awaited()

    async def test_reconnecting_channel_not_touched(self) -> None:
        """适配器自愈中，看门狗不抢方向盘。"""
        ch = _channel(ChannelStatus.RECONNECTING)
        mgr = _FakeManager({"qq": ch})
        sup = ChannelSupervisor(mgr, interval=999)
        await sup._inspect_once()
        mgr.start_channel.assert_not_awaited()

    async def test_degraded_channel_skipped(self) -> None:
        ch = _channel(ChannelStatus.ERROR)
        mgr = _FakeManager({"qq": ch})
        sup = ChannelSupervisor(mgr, interval=999)
        sup._degraded.add("qq")
        await sup._inspect_once()
        mgr.start_channel.assert_not_awaited()


class TestRestartBackoff:
    async def test_max_restarts_marks_degraded(self) -> None:
        ch = _channel(ChannelStatus.ERROR)
        mgr = _FakeManager({"qq": ch})
        sup = ChannelSupervisor(mgr, interval=999, max_restarts=3)
        sup._fail_counts["qq"] = 3
        await sup._restart_with_backoff("qq", ch)
        assert "qq" in sup._degraded
        mgr.start_channel.assert_not_awaited()

    async def test_failed_restart_increments_count(self) -> None:
        ch = _channel(ChannelStatus.ERROR)
        mgr = _FakeManager({"qq": ch})
        mgr.start_channel.return_value = False
        sup = ChannelSupervisor(mgr, interval=999, base_backoff=0)
        await sup._restart_with_backoff("qq", ch)
        assert sup._fail_counts["qq"] == 1

    async def test_successful_restart_clears_count(self) -> None:
        ch = _channel(ChannelStatus.ERROR)
        mgr = _FakeManager({"qq": ch})
        sup = ChannelSupervisor(mgr, interval=999, base_backoff=0)
        sup._fail_counts["qq"] = 2
        await sup._restart_with_backoff("qq", ch)
        assert "qq" not in sup._fail_counts

    async def test_status_changed_during_backoff_aborts(self) -> None:
        """退避期间频道已被其他路径恢复 → 放弃本次重启。"""
        ch = _channel(ChannelStatus.ERROR)
        mgr = _FakeManager({"qq": ch})

        async def _heal(*_a, **_k) -> None:
            ch._status = ChannelStatus.RUNNING

        sup = ChannelSupervisor(mgr, interval=999, base_backoff=0.01)
        import asyncio
        original_sleep = asyncio.sleep

        async def _sleep(s: float) -> None:
            await _heal()
            await original_sleep(0)

        import agent.channel.supervision as mod
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(mod.asyncio, "sleep", _sleep)
            await sup._restart_with_backoff("qq", ch)
        mgr.start_channel.assert_not_awaited()


class TestStatusInfo:
    def test_status_snapshot(self) -> None:
        sup = ChannelSupervisor(_FakeManager({}), interval=999)
        sup._fail_counts["qq"] = 2
        sup._degraded.add("wx")
        info = sup.get_status_info()
        assert info["fail_counts"] == {"qq": 2}
        assert info["degraded"] == ["wx"]
        assert info["running"] is False
