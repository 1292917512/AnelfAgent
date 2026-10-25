"""agent/channel/hotplug.py 频道目录热插拔同步器单元测试。

锁定：sync_channels 的 reconcile 语义（增/删/重载路由、enabled 才激活、
失败目录下轮重试、单飞护栏）、拆除链（停止 → 注销 → schema 回收 → sys.modules）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from agent.channel import hotplug
from core.config import ConfigManager, ConfigRegistry


class _FakeManager:
    """ChannelManager 替身：记录启停/激活/注销调用。"""

    def __init__(self, channels: Dict[str, Any]) -> None:
        self._channels = channels
        self.activated: List[str] = []
        self.stopped: List[str] = []
        self.unregistered: List[str] = []
        self.reloaded: List[str] = []

    def list_channels(self) -> Dict[str, Any]:
        return dict(self._channels)

    async def activate_channel(self, channel_id: str) -> bool:
        self.activated.append(channel_id)
        self._channels[channel_id] = object()
        return True

    async def stop_channel(self, channel_id: str) -> bool:
        self.stopped.append(channel_id)
        return True

    def unregister(self, channel_id: str) -> None:
        self.unregistered.append(channel_id)
        self._channels.pop(channel_id, None)

    async def reload_channel(self, channel_id: str) -> None:
        self.reloaded.append(channel_id)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    """隔离 hotplug 全局态与配置面（不触碰真实 channels/ 目录与注册表）。"""
    monkeypatch.setattr(hotplug, "_sync_running", False)
    monkeypatch.setattr(hotplug, "_synced_channels", None)
    monkeypatch.setattr(hotplug, "_failed_channels", set())
    monkeypatch.setattr(ConfigRegistry, "get_all_groups", classmethod(lambda cls: []))
    # 静默事件广播（测试无 web 订阅方）
    monkeypatch.setattr(hotplug, "_emit", AsyncMock())
    yield
    for key in ("c_new_enabled", "c_old_enabled", "c1_enabled"):
        ConfigManager.set(key, None)


def _patch_schema_ops(monkeypatch: pytest.MonkeyPatch) -> Dict[str, List[str]]:
    calls: Dict[str, List[str]] = {"registered": [], "unregistered": []}
    monkeypatch.setattr(
        hotplug, "register_channel_schema",
        lambda cid: calls["registered"].append(cid) is None or True,
    )
    monkeypatch.setattr(
        hotplug, "unregister_channel_schema",
        lambda cid: calls["unregistered"].append(cid),
    )
    return calls


class TestScanChannelDirs:
    def test_only_dirs_with_adapter_py(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "alpha").mkdir()
        (tmp_path / "alpha" / "adapter.py").touch()
        (tmp_path / "beta").mkdir()  # 无 adapter.py
        (tmp_path / "_private").mkdir()
        (tmp_path / "_private" / "adapter.py").touch()
        monkeypatch.setattr(hotplug, "channels_dir", lambda: tmp_path)

        assert hotplug.scan_channel_dirs() == {"alpha"}


class TestSyncChannels:
    async def test_failed_activation_is_reported_and_retried_with_registered_instance(self, monkeypatch):
        _patch_schema_ops(monkeypatch)
        manager = _FakeManager({})
        monkeypatch.setattr(hotplug, "get_channel_manager", lambda: manager)
        monkeypatch.setattr(hotplug, "scan_channel_dirs", lambda: {"c_new"})
        ConfigManager.set("c_new_enabled", True)

        async def fail(cid):
            manager._channels[cid] = object()
            return False

        monkeypatch.setattr(manager, "activate_channel", fail)
        result = await hotplug.sync_channels()
        assert result["added"] == [] and result["failed"] == ["c_new"]
        monkeypatch.setattr(manager, "activate_channel", AsyncMock(return_value=True))
        result = await hotplug.sync_channels()
        assert result["reloaded"] == ["c_new"] and result["failed"] == []

    async def test_failed_stop_preserves_channel_and_schema(self, monkeypatch):
        calls = _patch_schema_ops(monkeypatch)
        instance = object()
        manager = _FakeManager({"c_old": instance})
        monkeypatch.setattr(hotplug, "get_channel_manager", lambda: manager)
        monkeypatch.setattr(hotplug, "scan_channel_dirs", lambda: set())
        monkeypatch.setattr(manager, "stop_channel", AsyncMock(return_value=False))
        result = await hotplug.sync_channels()
        assert result["removed"] == [] and result["failed"] == ["c_old"]
        assert manager._channels["c_old"] is instance
        assert calls["unregistered"] == []
        monkeypatch.setattr(manager, "stop_channel", AsyncMock(return_value=True))
        result = await hotplug.sync_channels()
        assert result["removed"] == ["c_old"] and result["failed"] == []

    async def test_add_disabled_channel_registers_schema_only(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _patch_schema_ops(monkeypatch)
        manager = _FakeManager({})
        monkeypatch.setattr(hotplug, "get_channel_manager", lambda: manager)
        monkeypatch.setattr(hotplug, "scan_channel_dirs", lambda: {"c_new"})

        result = await hotplug.sync_channels()
        assert result["added"] == ["c_new"]
        assert calls["registered"] == ["c_new"]
        assert manager.activated == []  # 未启用不激活

    async def test_add_enabled_channel_activates(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _patch_schema_ops(monkeypatch)
        manager = _FakeManager({})
        monkeypatch.setattr(hotplug, "get_channel_manager", lambda: manager)
        monkeypatch.setattr(hotplug, "scan_channel_dirs", lambda: {"c_new"})
        ConfigManager.set("c_new_enabled", True)

        result = await hotplug.sync_channels()
        assert result["added"] == ["c_new"]
        assert calls["registered"] == ["c_new"]
        assert manager.activated == ["c_new"]

    async def test_remove_channel_full_teardown(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _patch_schema_ops(monkeypatch)
        manager = _FakeManager({"c_old": object()})
        monkeypatch.setattr(hotplug, "get_channel_manager", lambda: manager)
        monkeypatch.setattr(hotplug, "scan_channel_dirs", lambda: set())
        # 伪造已加载模块，验证清理
        sys.modules["channels.c_old"] = object()  # type: ignore[assignment]
        sys.modules["channels.c_old.adapter"] = object()  # type: ignore[assignment]

        result = await hotplug.sync_channels()
        assert result["removed"] == ["c_old"]
        assert manager.stopped == ["c_old"]
        assert manager.unregistered == ["c_old"]
        assert calls["unregistered"] == ["c_old"]
        assert "channels.c_old" not in sys.modules
        assert "channels.c_old.adapter" not in sys.modules

    async def test_reload_existing_restarts_with_fresh_code(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _patch_schema_ops(monkeypatch)
        manager = _FakeManager({"c1": object()})
        monkeypatch.setattr(hotplug, "get_channel_manager", lambda: manager)
        monkeypatch.setattr(hotplug, "scan_channel_dirs", lambda: {"c1"})
        ConfigManager.set("c1_enabled", True)

        # 目录监听语义：不因代码变更打断运行
        result = await hotplug.sync_channels(reload_existing=False)
        assert result["reloaded"] == [] and manager.stopped == []

        result = await hotplug.sync_channels(reload_existing=True)
        assert result["reloaded"] == ["c1"]
        assert manager.reloaded == ["c1"]
        assert manager.stopped == [] and calls["registered"] == []

    async def test_failed_add_retried_next_sync(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        manager = _FakeManager({})
        monkeypatch.setattr(hotplug, "get_channel_manager", lambda: manager)
        monkeypatch.setattr(hotplug, "scan_channel_dirs", lambda: {"c_bad"})

        fail = True

        def _schema(cid: str) -> bool:
            if fail:
                raise RuntimeError("boom")
            return True

        monkeypatch.setattr(hotplug, "register_channel_schema", _schema)

        result = await hotplug.sync_channels()
        assert result["failed"] == ["c_bad"]

        fail = False
        result = await hotplug.sync_channels()
        assert result["reloaded"] == ["c_bad"]
        assert result["failed"] == []

    async def test_singleflight_skip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(hotplug, "_sync_running", True)
        result = await hotplug.sync_channels()
        assert result["skipped"] == "in_progress"

    async def test_targeted_reload_leaves_unrelated_add_remove_and_retry_pending(self, monkeypatch):
        manager = _FakeManager({"c1": object(), "removed": object()})
        monkeypatch.setattr(hotplug, "get_channel_manager", lambda: manager)
        monkeypatch.setattr(hotplug, "scan_channel_dirs", lambda: {"c1", "new", "failed"})
        monkeypatch.setattr(hotplug, "_synced_channels", {"removed", "c1"})
        monkeypatch.setattr(hotplug, "_failed_channels", {"failed"})
        result = await hotplug.sync_channels(True, channel_id="c1")
        assert result == {"added": [], "removed": [], "reloaded": ["c1"], "failed": [], "total": 3}
        assert manager.reloaded == ["c1"] and manager.unregistered == []
        assert hotplug._failed_channels == {"failed"}
        assert hotplug._synced_channels == {"removed", "c1"}

    @pytest.mark.parametrize("name", ["missing", "../c1", "c1.adapter"])
    async def test_invalid_target_does_not_fall_back_to_global_sync(self, monkeypatch, name):
        monkeypatch.setattr(hotplug, "scan_channel_dirs", lambda: {"c1"})
        with pytest.raises(KeyError):
            await hotplug.sync_channels(True, channel_id=name)
