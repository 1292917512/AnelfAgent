"""AI 热同步接口：原同步器路由、权限门控、部分失败和取消契约。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

import entities
from core.config import ConfigManager
from core.entity import EntityRegistry
from entities import _sdk
from entities.devops import module_reload, tools


def summary(**fields):
    return {"added": [], "removed": [], "reloaded": [], "failed": [], "total": 1, **fields}


@pytest.mark.parametrize("name", ["reload_entity", "reload_channel", "sync_modules"])
def test_registration_uses_existing_critical_approval_contract(name):
    metadata = EntityRegistry.get(name)
    assert metadata is not None and metadata.group == "devops"
    assert metadata.meta["risk"] == "CRITICAL"
    assert not metadata.meta.get("concurrency_safe")
    if name == "reload_channel":
        assert metadata.check_fn is _sdk.channel_sensitive_allowed


async def test_targeted_entity_reload_forwards_actual_module_name(monkeypatch):
    reload = AsyncMock(return_value=summary(reloaded=["filesystem"]))
    monkeypatch.setattr(entities, "reload_entities", reload)
    payload = json.loads(await tools.reload_entity(" filesystem "))
    reload.assert_awaited_once_with(reload_existing=True, entity_name="filesystem")
    assert payload["ok"] and payload["results"]["entities"]["reloaded"] == ["filesystem"]
    assert json.loads(await tools.reload_entity(" "))["cause"] == "param"
    reload.side_effect = ValueError("missing module")
    assert json.loads(await tools.reload_entity("missing"))["cause"] == "not_found"


async def test_channel_reload_reuses_bridge_and_current_channel_warning(monkeypatch):
    reload = AsyncMock(return_value=summary(reloaded=["qq"]))
    monkeypatch.setattr(module_reload, "sync_channel_modules", reload)
    monkeypatch.setattr(module_reload, "get_current_channel", lambda: "qq")
    payload = json.loads(await tools.reload_channel(" qq "))
    reload.assert_awaited_once_with(reload_existing=True, channel_id="qq")
    assert payload["ok"] and payload["results"]["channels"]["warning"]
    ConfigManager.set("channel_tools_allow_sensitive", False)
    assert json.loads(await tools.reload_channel("qq"))["cause"] == "permission"
    assert reload.await_count == 1


async def test_batch_permission_preflight_precedes_all_mutations(monkeypatch):
    entity = AsyncMock(return_value=summary())
    channel = AsyncMock(return_value=summary())
    monkeypatch.setattr(entities, "reload_entities", entity)
    monkeypatch.setattr(module_reload, "sync_channel_modules", channel)
    ConfigManager.set("channel_tools_allow_sensitive", False)
    assert json.loads(await tools.sync_modules())["cause"] == "permission"
    entity.assert_not_awaited()
    channel.assert_not_awaited()
    assert json.loads(await tools.sync_modules("entities"))["ok"]
    entity.assert_awaited_once_with(reload_existing=False)


@pytest.mark.parametrize("reload_existing", [False, True])
async def test_batch_preserves_partial_failure_and_busy_results(monkeypatch, reload_existing):
    entity = AsyncMock(return_value=summary(reloaded=["one"], failed=["two"]))
    channel = AsyncMock(return_value=summary(skipped="in_progress"))
    monkeypatch.setattr(entities, "reload_entities", entity)
    monkeypatch.setattr(module_reload, "sync_channel_modules", channel)
    payload = json.loads(await tools.sync_modules("all", reload_existing))
    entity.assert_awaited_once_with(reload_existing=reload_existing)
    channel.assert_awaited_once_with(reload_existing=reload_existing)
    assert payload["error"] and payload["cause"] == "state" and not payload["retryable"]
    assert payload["results"]["entities"]["reloaded"] == ["one"]
    assert payload["results"]["channels"]["skipped"] == "in_progress"


async def test_batch_continues_other_target_on_exception_but_propagates_cancellation(monkeypatch):
    entity = AsyncMock(side_effect=RuntimeError("broken"))
    channel = AsyncMock(return_value=summary(added=["new"]))
    monkeypatch.setattr(entities, "reload_entities", entity)
    monkeypatch.setattr(module_reload, "sync_channel_modules", channel)
    payload = json.loads(await tools.sync_modules("all"))
    assert payload["results"]["entities"]["cause"] == "internal"
    assert payload["results"]["channels"]["added"] == ["new"]
    entity.side_effect = asyncio.CancelledError()
    channel.reset_mock()
    with pytest.raises(asyncio.CancelledError):
        await tools.sync_modules("all")
    channel.assert_not_awaited()


async def test_channel_bridge_calls_original_synchronizer(monkeypatch):
    from agent.channel import hotplug

    reload = AsyncMock(return_value=summary())
    monkeypatch.setattr(hotplug, "sync_channels", reload)
    await _sdk.sync_channel_modules(True, channel_id="qq")
    reload.assert_awaited_once_with(reload_existing=True, channel_id="qq")


async def test_targeted_receipt_discovers_current_tools_even_after_rollback(monkeypatch):
    def probe() -> str:
        return "old implementation"

    probe.__module__ = "entities.discovery_probe.tools"
    name = "devops_reload_discovery_probe"
    EntityRegistry.register_tool(name=name, func=probe, group="discovery_probe")
    monkeypatch.setattr(entities, "reload_entities", AsyncMock(return_value=summary(failed=["discovery_probe"])))
    try:
        payload = json.loads(await tools.reload_entity("discovery_probe"))
        assert payload["error"] and payload["current_tools"] == [{"group": "discovery_probe", "names": [name]}]
        assert "list_entity_methods" in payload["next_step"]
    finally:
        EntityRegistry.unregister(name)
        EntityRegistry.unregister_group("discovery_probe")
