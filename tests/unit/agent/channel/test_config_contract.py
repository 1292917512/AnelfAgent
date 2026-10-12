"""频道配置以完整模型校验，成功落盘后才发布给运行实例。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import Field, model_validator

from agent.channel.base import BaseChannel, ChannelConfig
from agent.channel.channel_types import ChannelStatus
from agent.channel.config import ChannelConfigStore
from agent.channel.config_watcher import ConfigWatcher
from agent.channel.manager import ChannelManager
from core.config import ConfigManager, ConfigRegistry, register_model_configs
from core.entity import EntityRegistry


class Config(ChannelConfig):
    names: list[str] = Field(default_factory=list)
    weights: dict[str, int] = Field(default_factory=dict)
    note: str | None = None
    lower: int = 1
    upper: int = 10

    @model_validator(mode="after")
    def ordered(self):
        if self.lower >= self.upper:
            raise ValueError("lower must be less than upper")
        return self


class Channel(BaseChannel):
    channel_id = "contract"
    display_name = "Contract"
    capabilities = set()
    _Configs = Config

    async def start(self):
        pass

    async def stop(self):
        pass

    async def forward_message(self, request):
        raise NotImplementedError

    async def get_self_info(self):
        raise NotImplementedError

    async def get_channel_info(self, channel_id):
        raise NotImplementedError

    async def health_check(self):
        raise NotImplementedError


@pytest.fixture
def config_store(tmp_path):
    path = tmp_path / "channel.json"
    path.write_text('{"names":["original"],"lower":1,"upper":10}', encoding="utf-8")
    store = ChannelConfigStore("contract", path, Config)
    ConfigManager.register_store("contract_", store)
    register_model_configs("adapter/contract", Config, key_prefix="contract")
    yield store
    ConfigRegistry.unregister_group("adapter/contract")


def test_generic_collections_and_nullable_value(config_store):
    assert ConfigRegistry.get_item("contract_names").type_name == "json"
    assert ConfigRegistry.get_item("contract_weights").type_name == "json"
    ConfigManager.set_persisted({"contract_names": ["a", "b"], "contract_note": None})
    saved = json.loads(config_store.path.read_text("utf-8"))
    assert saved["names"] == ["a", "b"] and saved["note"] is None


def test_batch_validation_precedes_notification_and_failed_write_keeps_values(config_store):
    seen = []
    ConfigManager.add_listener("contract_", lambda key, value: seen.append((
        ConfigManager.get("contract_lower"), ConfigManager.get("contract_upper"))))
    ConfigManager.set_persisted({"contract_lower": 20, "contract_upper": 30})
    assert seen == [(20, 30), (20, 30)]
    original = config_store.path.read_bytes()
    seen.clear()
    with pytest.raises(ValueError, match="lower must be less"):
        ConfigManager.set_persisted({"contract_upper": 5})
    assert config_store.path.read_bytes() == original
    assert ConfigManager.get("contract_upper") == 30
    assert seen == []


def test_invalid_external_edit_retains_live_values(config_store):
    config_store.path.write_text('{"names":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="names"):
        config_store.reload_notify()
    assert ConfigManager.get("contract_names") == ["original"]


def test_invalid_model_does_not_reset_other_channel_fields(config_store):
    channel = Channel()
    try:
        config_store._values["names"] = False
        assert channel.reload_config() is False
        assert channel.config.names == ["original"]
        assert channel.config.upper == 10
    finally:
        ConfigManager.remove_listener("contract_", channel._on_config_changed)
        EntityRegistry.unregister(channel.get_entity_name())


def test_replaced_store_updates_existing_watch_callback(config_store):
    watcher = ConfigWatcher()
    path = str(config_store.path)
    watcher.watch(path, config_store.reload_notify)
    replacement = ChannelConfigStore("contract", config_store.path, Config)
    ConfigManager.register_store("contract_", replacement)
    watcher.watch(path, replacement.reload_notify)
    config_store.path.write_text('{"names":["changed"]}', encoding="utf-8")
    watcher._callbacks[path]()
    assert ConfigManager.get("contract_names") == ["changed"]
    assert config_store.get("contract_names") == ["original"]
    watcher.unwatch(path)


@pytest.fixture
def managed_channel(config_store):
    manager = ChannelManager()
    channel = Channel()
    manager.register(channel)
    yield manager, channel
    manager.unregister(channel.channel_id)
    EntityRegistry.unregister(manager.get_entity_name())


async def test_cancelled_start_cleans_partial_resources(managed_channel):
    manager, channel = managed_channel
    entered = asyncio.Event()

    async def start():
        entered.set()
        await asyncio.Event().wait()

    channel.start = start
    channel.stop = AsyncMock()
    task = asyncio.create_task(manager.start_channel(channel.channel_id))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert channel.status is ChannelStatus.ERROR
    channel.stop.assert_awaited_once()


async def test_concurrent_start_only_opens_one_connection(managed_channel):
    manager, channel = managed_channel
    channel.start = AsyncMock()
    results = await asyncio.gather(*(manager.start_channel(channel.channel_id) for _ in range(5)))
    assert all(results)
    channel.start.assert_awaited_once()


async def test_stopping_error_channel_cannot_be_restarted_by_queued_watchdog(managed_channel):
    manager, channel = managed_channel
    channel._status = ChannelStatus.ERROR
    channel.start = AsyncMock()
    channel.stop = AsyncMock(side_effect=RuntimeError("still shutting down"))
    lock = manager._operation_lock(channel.channel_id)
    await lock.acquire()
    restart = asyncio.create_task(manager.restart_channel(channel.channel_id, channel))
    await asyncio.sleep(0)
    stop = asyncio.create_task(manager.stop_channel(channel.channel_id))
    await asyncio.sleep(0)
    lock.release()
    assert await restart is False
    assert await stop is False
    channel.start.assert_not_awaited()
    assert not manager.should_restart(channel.channel_id, channel)


def test_unregistered_adapter_is_removed_from_entity_registry(managed_channel):
    manager, channel = managed_channel
    name = channel.get_entity_name()
    assert EntityRegistry.exists(name)
    manager.unregister(channel.channel_id)
    assert not EntityRegistry.exists(name)


def test_constructor_failure_releases_adapter_and_config_listener(config_store):
    from core.registration import registration_transaction

    class Broken(Channel):
        __module__ = "channels.contract.adapter"

        def __init__(self):
            super().__init__()
            raise RuntimeError("constructor failed")

    before = {entry.name for entry in EntityRegistry.get_all()}
    listeners = list(ConfigManager._listeners.get("contract_", []))
    with pytest.raises(RuntimeError, match="constructor failed"):
        with registration_transaction("channels.contract"):
            Broken()
    assert {entry.name for entry in EntityRegistry.get_all()} == before
    assert ConfigManager._listeners.get("contract_", []) == listeners
