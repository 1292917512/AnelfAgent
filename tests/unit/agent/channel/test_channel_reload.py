"""真实临时频道验证源码刷新、工具重绑、失败恢复和生命周期竞态。"""

from __future__ import annotations

import asyncio
import json
import os
import py_compile
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import agent.channel.config as config
import agent.channel.config_watcher as watcher_module
import agent.channel.manager as manager_module
import agent.channel.reload as reload_module
import channels
from agent.channel.channel_types import ChannelStatus
from agent.channel.tool_bridge import get_channel_tool_info, set_channel_tool_state
from core.config import ConfigManager
from core.entity import EntityRegistry
from core.lifecycle import Lifecycle


def _write_channel(root: Path, cid: str, version: str = "v1", *, start: str = "pass", stop: str = "pass") -> Path:
    directory = root / cid
    directory.mkdir(exist_ok=True)
    (directory / "__init__.py").touch()
    (directory / "helper.py").write_text(f'VERSION = "{version}"\n', encoding="utf-8")
    (directory / "config.py").write_text(
        "from pydantic import Field\nfrom agent.channel.base import ChannelConfig\n"
        "class Config(ChannelConfig):\n"
        "    enabled: bool = True\n"
        f"    label: str = Field(default='default', description='{version}')\n"
        "CONFIG_MODEL = Config\n", encoding="utf-8",
    )
    (directory / "adapter.py").write_text(
        "import json\nfrom agent.channel.base import BaseChannel\n"
        "from agent.channel.channel_types import ChannelCapability\n"
        "from agent.channel.tool_bridge import channel_tool\n"
        "from .config import Config\nfrom .helper import VERSION\n"
        "class Channel(BaseChannel):\n"
        f"    channel_id = '{cid}'\n    display_name = '{cid}'\n"
        "    capabilities = {ChannelCapability.DELETE_MESSAGE}\n    _Configs = Config\n"
        "    async def start(self):\n"
        f"        {start}\n"
        "    async def stop(self):\n"
        f"        {stop}\n"
        "    async def forward_message(self, request): pass\n"
        "    async def get_self_info(self): pass\n"
        "    async def get_channel_info(self, channel_id): pass\n"
        "    async def health_check(self): pass\n"
        "    @channel_tool()\n"
        "    async def inspect_version(self) -> str:\n"
        "        return json.dumps({'version': VERSION})\n"
        "    @channel_tool()\n"
        "    async def delete_message(self, chat_id: str, message_id: str) -> str:\n"
        "        return json.dumps({'version': VERSION, 'success': True})\n"
        "CHANNEL_CLASS = Channel\n", encoding="utf-8",
    )
    config_file = directory / "channel_config.json"
    if not config_file.exists():
        config_file.write_text(json.dumps({"enabled": True, "label": "persisted"}), encoding="utf-8")
    return directory


@pytest.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "channels"
    root.mkdir()
    monkeypatch.setattr(channels, "__path__", [str(root), *channels.__path__])
    for module in (config, manager_module, reload_module):
        monkeypatch.setattr(module, "channels_dir", lambda: root)
    watcher = watcher_module.ConfigWatcher()
    monkeypatch.setattr(watcher_module, "_watcher", watcher)
    for name in ("_instances", "_owners", "_critical_cleanups"):
        monkeypatch.setattr(Lifecycle, name, dict(getattr(Lifecycle, name)))
    for name in ("_order", "_cleanups", "_start_hooks", "_tick_hooks", "_quiesce_hooks"):
        monkeypatch.setattr(Lifecycle, name, list(getattr(Lifecycle, name)))
    monkeypatch.setattr(Lifecycle, "_started", False)
    manager = manager_module.ChannelManager()
    monkeypatch.setattr(manager_module, "_channel_manager", manager)
    _write_channel(root, "reload_demo")
    _write_channel(root, "reload_other")
    assert await manager.activate_channel("reload_demo")
    assert await manager.activate_channel("reload_other")
    yield root, manager, watcher
    await manager.stop_all()
    for cid in ("reload_demo", "reload_other"):
        manager.unregister(cid)
        config.unregister_channel_schema(cid)
        reload_module.pop_channel_modules(cid)
    watcher.stop_all()
    EntityRegistry.unregister(manager.get_entity_name())


async def _version(cid: str = "reload_demo") -> str:
    result = await EntityRegistry.execute_tool(f"{cid}_inspect_version")
    return str(json.loads(result)["version"])


async def test_reload_refreshes_private_dependencies_tools_and_config_without_restarting_others(runtime):
    root, manager, watcher = runtime
    previous = manager.get("reload_demo")
    other = manager.get("reload_other")
    manager._channel_map["reload_demo:room"] = "reload_demo"
    manager._group_targets.add("reload_demo:room")
    set_channel_tool_state("reload_demo", "delete_message", False)
    set_channel_tool_state("reload_demo", "reload_demo_inspect_version", False)
    old_task = watcher._watchers[str(root / "reload_demo" / "channel_config.json")]
    # 相同长度且相同 mtime，必须绕过 Python 的时间戳字节码命中。
    helper = root / "reload_demo" / "helper.py"
    stamp = helper.stat()
    py_compile.compile(str(helper), doraise=True)
    _write_channel(root, "reload_demo", "v2")
    os.utime(helper, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    await manager.reload_channel("reload_demo")
    current = manager.get("reload_demo")
    assert current is not previous and manager.get("reload_other") is other
    assert current.status == ChannelStatus.RUNNING and previous.status == ChannelStatus.STOPPED
    assert current.config.label == "persisted"
    assert manager._channel_map["reload_demo:room"] == "reload_demo"
    assert "reload_demo:room" in manager._group_targets
    assert watcher._watchers[str(root / "reload_demo" / "channel_config.json")] is old_task
    assert len(ConfigManager._listeners["reload_demo_"]) == 1
    assert not next(tool for tool in get_channel_tool_info("reload_demo") if tool["name"] == "delete_message")["enabled"]
    assert not EntityRegistry.get("reload_demo_inspect_version").enabled
    set_channel_tool_state("reload_demo", "reload_demo_inspect_version", True)
    EntityRegistry.enable("reload_demo_inspect_version")
    assert await _version() == "v2" and await _version("reload_other") == "v1"
    common = json.loads(await EntityRegistry.execute_tool("delete_message", json.dumps({
        "channel_id": "reload_other", "target_id": "room", "message_id": "m",
    })))
    assert common["version"] == "v1"


@pytest.mark.parametrize("failure", ["syntax", "import", "config", "start"])
async def test_reload_failure_restores_original_code_schema_listener_and_tools(runtime, failure):
    root, manager, watcher = runtime
    previous = manager.get("reload_demo")
    module = sys.modules["channels.reload_demo.adapter"]
    store = ConfigManager._store_for("reload_demo_label")
    callback = watcher._callbacks[str(root / "reload_demo" / "channel_config.json")]
    _write_channel(root, "reload_demo", "v2", start="raise RuntimeError('bad start')" if failure == "start" else "pass")
    if failure == "syntax":
        (root / "reload_demo" / "helper.py").write_text("broken (", encoding="utf-8")
    elif failure == "import":
        (root / "reload_demo" / "helper.py").write_text("import missing_channel_dependency\n", encoding="utf-8")
    elif failure == "config":
        (root / "reload_demo" / "channel_config.json").write_text('{"enabled": true, "label": []}', encoding="utf-8")
    with pytest.raises((RuntimeError, ValueError, SyntaxError)):
        await manager.reload_channel("reload_demo")
    assert manager.get("reload_demo") is previous and previous.status == ChannelStatus.RUNNING
    assert sys.modules["channels.reload_demo.adapter"] is module
    assert channels.reload_demo.adapter is module
    assert ConfigManager._store_for("reload_demo_label") is store
    assert watcher._callbacks[str(root / "reload_demo" / "channel_config.json")] == callback
    assert len(ConfigManager._listeners["reload_demo_"]) == 1
    assert await _version() == "v1"


async def test_stop_during_reload_and_stale_watchdog_cannot_restart_replacement(runtime, monkeypatch):
    root, manager, _ = runtime
    previous = manager.get("reload_demo")
    entered, release = asyncio.Event(), asyncio.Event()

    async def stop():
        entered.set()
        await release.wait()

    monkeypatch.setattr(previous, "stop", stop)
    _write_channel(root, "reload_demo", "v2")
    reloading = asyncio.create_task(manager.reload_channel("reload_demo"))
    await entered.wait()
    stopping = asyncio.create_task(manager.stop_channel("reload_demo"))
    watchdog = asyncio.create_task(manager.restart_channel("reload_demo", previous))
    await asyncio.sleep(0)
    release.set()
    await reloading
    assert await stopping
    assert not await watchdog
    assert manager.get("reload_demo").status == ChannelStatus.STOPPED
    assert not manager.should_restart("reload_demo", manager.get("reload_demo"))


async def test_failed_stop_keeps_original_instance_managed(runtime, monkeypatch):
    root, manager, _ = runtime
    previous = manager.get("reload_demo")
    monkeypatch.setattr(previous, "stop", AsyncMock(side_effect=RuntimeError("busy")))
    _write_channel(root, "reload_demo", "v2")
    with pytest.raises(RuntimeError, match="停止失败"):
        await manager.reload_channel("reload_demo")
    assert manager.get("reload_demo") is previous
    assert await _version() == "v1"


async def test_unclean_failed_start_retains_new_resource_handle_for_retry(runtime):
    root, manager, _ = runtime
    previous = manager.get("reload_demo")
    _write_channel(root, "reload_demo", "v2", start="raise RuntimeError('start')", stop="raise RuntimeError('stop')")
    with pytest.raises(RuntimeError, match="资源未完全关闭"):
        await manager.reload_channel("reload_demo")
    current = manager.get("reload_demo")
    assert current is not previous and current.status == ChannelStatus.ERROR
    assert previous.status == ChannelStatus.STOPPED
    assert sys.modules["channels.reload_demo.helper"].VERSION == "v2"
    assert get_channel_tool_info("reload_demo") == []


async def test_reload_disabled_channel_refreshes_schema_without_starting(runtime):
    root, manager, _ = runtime
    assert await manager.stop_channel("reload_demo")
    manager.unregister("reload_demo")
    config.set_channel_config("reload_demo", enabled=False)
    _write_channel(root, "reload_demo", "v2", start="raise AssertionError('must stay disabled')")
    await manager.reload_channel("reload_demo")
    assert manager.get("reload_demo") is None
    assert ConfigManager.get("reload_demo_label") == "persisted"
    assert ConfigManager.get("reload_demo_enabled") is False


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_cancelled_start_preserves_cancellation_and_resource_ownership(runtime, monkeypatch, cleanup_fails):
    root, manager, _ = runtime
    previous = manager.get("reload_demo")
    _write_channel(root, "reload_demo", "v2")
    entered = asyncio.Event()
    start = manager._start_channel

    async def block_replacement(cid):
        if cid == "reload_demo" and manager.get(cid) is not previous:
            if cleanup_fails:
                monkeypatch.setattr(manager.get(cid), "stop", AsyncMock(side_effect=RuntimeError("busy")))
            entered.set()
            await asyncio.Event().wait()
        return await start(cid)

    monkeypatch.setattr(manager, "_start_channel", block_replacement)
    task = asyncio.create_task(manager.reload_channel("reload_demo"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    if cleanup_fails:
        assert manager.get("reload_demo") is not previous
        assert manager.get("reload_demo").status == ChannelStatus.ERROR
        assert get_channel_tool_info("reload_demo") == []
    else:
        assert manager.get("reload_demo") is previous and previous.status == ChannelStatus.RUNNING
        assert await _version() == "v1"


async def test_lifecycle_owns_auxiliary_cleanup_and_blocks_reload_when_cleanup_fails(runtime, monkeypatch):
    _root, manager, _watcher = runtime
    previous = manager.get("reload_demo")
    stop = AsyncMock()
    monkeypatch.setattr(previous, "stop", stop)
    cleanup = AsyncMock(side_effect=RuntimeError("client still open"))
    Lifecycle.register("reload_demo.session", object(), cleanup=cleanup)
    Lifecycle._owners["reload_demo.session"] = "channels.reload_demo.login"
    with pytest.raises(RuntimeError, match="资源关闭失败"):
        await manager.reload_channel("reload_demo")
    stop.assert_not_awaited()
    assert "reload_demo.session" in {entry["name"] for entry in Lifecycle.snapshot()}
    assert manager.get("reload_demo") is previous and await _version() == "v1"
    cleanup.side_effect = None
    await manager.reload_channel("reload_demo")
    assert "reload_demo.session" not in {entry["name"] for entry in Lifecycle.snapshot()}
