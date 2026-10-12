"""按需感知、会话隔离、状态真实性与快照预算的回归验证。"""
from __future__ import annotations

from dataclasses import replace

import pytest

from agent.mind.tool_activation import tool_activation
from core.config import ConfigManager
from core.entity import EntityRegistry
from entities.smart_home import context, framework
from entities.smart_home.models import DeviceState


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch):
    devices = [
        DeviceState("light.b", "light", "床头灯", "off", area="卧室"),
        DeviceState("light.a", "light", "台灯", "on", area="书房"),
    ]

    class Home:
        def status(self):
            return {"configured": True, "connected": True}
        def devices(self):
            return list(devices)

    monkeypatch.setattr(context, "get_smart_home_manager", Home)
    monkeypatch.setattr(EntityRegistry, "group_has_enabled_tools", lambda _: True)
    ConfigManager.set("smart_home_context_inject", True)
    ConfigManager.set("smart_home_context_mode", "on_demand")
    tool_activation.clear_scope("home-test")
    tool_activation.clear_scope("other-test")
    yield devices
    tool_activation.clear_scope("home-test")
    tool_activation.clear_scope("other-test")


@pytest.mark.asyncio
async def test_sleep_activate_scope_isolation_and_close(home):
    provider = context.SmartHomeContextProvider()
    assert await provider.provide("home-test") is None
    tool_activation.activate("smart_home", scope="home-test")
    snapshot = await provider.provide("home-test")
    assert snapshot is not None and "台灯" in snapshot.content
    assert await provider.provide("other-test") is None
    tool_activation.deactivate("smart_home", scope="home-test")
    assert await provider.provide("home-test") is None


def test_preview_does_not_wake_and_respects_master_switch(home):
    assert "台灯" in context.context_content(preview=True)
    assert not tool_activation.is_active("smart_home", "home-test")
    ConfigManager.set("smart_home_context_inject", False)
    assert context.context_content(preview=True) == ""


def test_always_mode_and_disabled_entity(home, monkeypatch):
    ConfigManager.set("smart_home_context_mode", "always")
    assert "台灯" in context.context_content("other-test")
    monkeypatch.setattr(EntityRegistry, "group_has_enabled_tools", lambda _: False)
    assert context.context_content("other-test") == ""


def test_stale_excluded_and_order_deterministic(home):
    home.append(DeviceState("light.old", "light", "过期灯", "on", stale=True))
    content = context.context_content(preview=True)
    assert "过期灯" not in content and "1 台设备状态已过期" in content
    assert framework.render_context(home) == framework.render_context(list(reversed(home)))
    assert "\n书房:\n- 灯光: 台灯: 开" in content


def test_large_snapshot_bounded_and_no_multiline_names(home):
    ConfigManager.set("smart_home_light_inject_limit", 0)
    devices = [replace(home[0], entity_id=f"light.{n}", name="设备\n" + "长" * 180 + str(n)) for n in range(1000)]
    content = framework.render_context(devices)
    assert len(content) <= 2400
    assert "分页查询" in content and "设备\n" not in content
