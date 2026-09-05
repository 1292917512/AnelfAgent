"""频道启停管理与 AI 启停工具（agent.channel.manager / manage_tools）单元测试。

覆盖：enabled 落盘与频道目录扫描、ChannelManager.activate_channel 动态加载、
start_channel/stop_channel 工具的状态分支与错误归因。
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock

import pytest

import agent.channel.context as channel_context
import agent.channel.manage_tools as manage_tools
import agent.channel.manager as manager_mod
from agent.channel.channel_types import ChannelStatus
from agent.channel.manager import ChannelManager
from core.config import ConfigManager
from core.entity import EntityRegistry


class FakeChannel:
    """最小频道替身：status/config + start/stop 计数。"""

    def __init__(self, channel_id: str = "fake",
                 status: ChannelStatus = ChannelStatus.STOPPED) -> None:
        self.channel_id = channel_id
        self.display_name = "Fake"
        self._status = status
        self.config = types.SimpleNamespace(enabled=True)
        self.started = 0
        self.stopped = 0

    @property
    def status(self) -> ChannelStatus:
        return self._status

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1


class FakeManager:
    """ChannelManager 替身：按 channel_id 持有单个频道，启停方法为 AsyncMock。"""

    def __init__(self, channel: Optional[FakeChannel] = None) -> None:
        self._channel = channel
        self.start_channel = AsyncMock(return_value=True)
        self.stop_channel = AsyncMock(return_value=True)
        self.activate_channel = AsyncMock(return_value=True)

    def get(self, channel_id: str) -> Optional[FakeChannel]:
        if self._channel is not None and self._channel.channel_id == channel_id:
            return self._channel
        return None


@pytest.fixture()
def channels_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """将频道目录隔离到 tmp_path。"""
    root = tmp_path / "channels"
    root.mkdir()
    monkeypatch.setattr(manager_mod, "channels_dir", lambda: root)
    return root


def _write_channel(root: Path, channel_id: str) -> None:
    channel_dir = root / channel_id
    channel_dir.mkdir(parents=True, exist_ok=True)
    (channel_dir / "adapter.py").write_text("# fake", "utf-8")


@pytest.fixture()
def patched_manager(monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """截获 manage_tools 对 manager 模块的延迟引用。"""
    state: Dict[str, Any] = {
        "cm": FakeManager(),
        "configured": {},
        "enabled_writes": [],
    }
    monkeypatch.setattr(manager_mod, "get_channel_manager", lambda: state["cm"])
    monkeypatch.setattr(
        manager_mod, "list_configured_channels", lambda: state["configured"],
    )

    def _set_enabled(channel_id: str, enabled: bool) -> bool:
        state["enabled_writes"].append((channel_id, enabled))
        return True

    monkeypatch.setattr(manager_mod, "set_channel_enabled", _set_enabled)
    return state


# ----------------------------------------------------------------------
# 频道启停配置（统一配置系统：<channel_id>_enabled 键）
# ----------------------------------------------------------------------

class TestConfiguredChannels:
    def test_scan_enabled_flags(self, channels_dir: Path) -> None:
        _write_channel(channels_dir, "qq")
        _write_channel(channels_dir, "tg")
        (channels_dir / "_scratch").mkdir()
        (channels_dir / "noadapter").mkdir()  # 无 adapter.py，跳过
        ConfigManager.set("qq_enabled", True)
        assert manager_mod.list_configured_channels() == {"qq": True, "tg": False}

    def test_scan_empty_dir(self, channels_dir: Path) -> None:
        assert manager_mod.list_configured_channels() == {}

    def test_set_enabled_persists(self) -> None:
        assert manager_mod.set_channel_enabled("qq", False) is True
        assert ConfigManager.get("qq_enabled") is False
        manager_mod.set_channel_enabled("qq", True)
        assert ConfigManager.get("qq_enabled") is True

    def test_set_enabled_notifies_listener(self) -> None:
        """写入经 ConfigManager 变更监听同步通知（频道热更的驱动路径）。"""
        fired: list = []
        ConfigManager.add_listener("qq_", lambda k, v: fired.append((k, v)))
        manager_mod.set_channel_enabled("qq", True)
        assert ("qq_enabled", True) in fired


# ----------------------------------------------------------------------
# ChannelManager.activate_channel
# ----------------------------------------------------------------------

class TestActivateChannel:
    async def test_registered_channel_delegates_to_start(self) -> None:
        cm = ChannelManager()
        channel = FakeChannel("fake")
        cm.register(channel)  # type: ignore[arg-type]
        assert await cm.activate_channel("fake") is True
        assert channel.started == 1
        assert channel.status == ChannelStatus.RUNNING

    async def test_missing_directory(self, channels_dir: Path) -> None:
        cm = ChannelManager()
        assert await cm.activate_channel("ghost") is False

    async def test_dynamic_load_and_start(
        self, channels_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_channel(channels_dir, "newbie")

        class NewbieChannel(FakeChannel):
            def __init__(self) -> None:
                super().__init__("newbie")

        mod = types.ModuleType("channels.newbie.adapter")
        mod.CHANNEL_CLASS = NewbieChannel  # type: ignore[attr-defined]
        monkeypatch.setattr(
            manager_mod.importlib, "import_module", lambda _name: mod,
        )

        cm = ChannelManager()
        assert await cm.activate_channel("newbie") is True
        channel = cm.get("newbie")
        assert channel is not None
        assert channel.status == ChannelStatus.RUNNING

    async def test_module_without_channel_class(
        self, channels_dir: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_channel(channels_dir, "empty")
        monkeypatch.setattr(
            manager_mod.importlib, "import_module",
            lambda _name: types.ModuleType("channels.empty.adapter"),
        )
        cm = ChannelManager()
        assert await cm.activate_channel("empty") is False
        assert cm.get("empty") is None


# ----------------------------------------------------------------------
# AI 工具：start_channel / stop_channel
# ----------------------------------------------------------------------

class TestStartChannelTool:
    async def test_empty_id_param_error(self, patched_manager: Dict[str, Any]) -> None:
        payload = json.loads(await manage_tools.start_channel("  "))
        assert payload["cause"] == "param"

    async def test_unknown_channel_not_found(self, patched_manager: Dict[str, Any]) -> None:
        payload = json.loads(await manage_tools.start_channel("ghost"))
        assert payload["cause"] == "not_found"

    async def test_already_running_is_idempotent(self, patched_manager: Dict[str, Any]) -> None:
        patched_manager["cm"] = FakeManager(FakeChannel("qq", ChannelStatus.RUNNING))
        payload = json.loads(await manage_tools.start_channel("qq"))
        assert payload["success"] is True
        patched_manager["cm"].start_channel.assert_not_awaited()
        assert patched_manager["enabled_writes"] == []

    async def test_start_registered_channel(self, patched_manager: Dict[str, Any]) -> None:
        patched_manager["cm"] = FakeManager(FakeChannel("qq"))
        payload = json.loads(await manage_tools.start_channel("qq"))
        assert payload["success"] is True
        patched_manager["cm"].start_channel.assert_awaited_once_with("qq")
        patched_manager["cm"].activate_channel.assert_not_awaited()
        assert patched_manager["enabled_writes"] == [("qq", True)]

    async def test_start_unregistered_channel_activates(
        self, patched_manager: Dict[str, Any],
    ) -> None:
        patched_manager["configured"] = {"tg": False}
        payload = json.loads(await manage_tools.start_channel("tg"))
        assert payload["success"] is True
        patched_manager["cm"].activate_channel.assert_awaited_once_with("tg")
        assert patched_manager["enabled_writes"] == [("tg", True)]

    async def test_start_failure_returns_state_error(
        self, patched_manager: Dict[str, Any],
    ) -> None:
        cm = FakeManager(FakeChannel("qq"))
        cm.start_channel = AsyncMock(return_value=False)
        patched_manager["cm"] = cm
        payload = json.loads(await manage_tools.start_channel("qq"))
        assert payload["cause"] == "state"
        assert payload["retryable"] is True


class TestStopChannelTool:
    async def test_unknown_channel_not_found(self, patched_manager: Dict[str, Any]) -> None:
        payload = json.loads(await manage_tools.stop_channel("ghost"))
        assert payload["cause"] == "not_found"

    async def test_stop_unregistered_marks_disabled(
        self, patched_manager: Dict[str, Any],
    ) -> None:
        patched_manager["configured"] = {"tg": True}
        payload = json.loads(await manage_tools.stop_channel("tg"))
        assert payload["success"] is True
        patched_manager["cm"].stop_channel.assert_not_awaited()
        assert patched_manager["enabled_writes"] == [("tg", False)]

    async def test_stop_running_channel(self, patched_manager: Dict[str, Any]) -> None:
        patched_manager["cm"] = FakeManager(FakeChannel("qq", ChannelStatus.RUNNING))
        payload = json.loads(await manage_tools.stop_channel("qq"))
        assert payload["success"] is True
        patched_manager["cm"].stop_channel.assert_awaited_once_with("qq")
        assert patched_manager["enabled_writes"] == [("qq", False)]
        assert "warning" not in payload

    async def test_stop_current_channel_warns(
        self, patched_manager: Dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        patched_manager["cm"] = FakeManager(FakeChannel("qq", ChannelStatus.RUNNING))
        monkeypatch.setattr(channel_context, "get_current_channel", lambda: "qq")
        payload = json.loads(await manage_tools.stop_channel("qq"))
        assert payload["success"] is True
        assert "warning" in payload

    async def test_stop_failure_returns_state_error(
        self, patched_manager: Dict[str, Any],
    ) -> None:
        cm = FakeManager(FakeChannel("qq", ChannelStatus.RUNNING))
        cm.stop_channel = AsyncMock(return_value=False)
        patched_manager["cm"] = cm
        payload = json.loads(await manage_tools.stop_channel("qq"))
        assert payload["cause"] == "state"


class TestRegistration:
    """模块导入即注册：分组描述 + 敏感门控 + 审批元数据。"""

    @pytest.mark.parametrize("name", ["start_channel", "stop_channel"])
    def test_tools_registered_with_sensitive_meta(self, name: str) -> None:
        entity = EntityRegistry.get(name)
        assert entity is not None
        assert entity.group == "channel_ops"
        assert entity.check_fn is not None
        assert entity.meta.get("risk") == "CRITICAL"
        assert "core" in entity.tags

    def test_group_description_registered(self) -> None:
        assert EntityRegistry.get_group_description("channel_ops")
