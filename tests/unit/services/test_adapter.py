"""频道切换在启动/重连期间仍表达停止意图，避免与热重载重复启动。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.adapter import AdapterService


@pytest.mark.parametrize("status", ["running", "starting", "reconnecting"])
async def test_toggle_active_channel_stops_and_persists_intent(monkeypatch, status):
    import agent.channel as channels
    import agent.channel.manager as manager_module

    manager = SimpleNamespace(
        get=lambda key: SimpleNamespace(status=SimpleNamespace(value=status)),
        stop_channel=AsyncMock(return_value=True), start_channel=AsyncMock(),
    )
    persist = Mock()
    monkeypatch.setattr(channels, "get_channel_manager", lambda: manager)
    monkeypatch.setattr(manager_module, "set_channel_enabled", persist)
    await AdapterService().toggle_adapter("demo")
    manager.stop_channel.assert_awaited_once_with("demo")
    manager.start_channel.assert_not_awaited()
    persist.assert_called_once_with("demo", False)
