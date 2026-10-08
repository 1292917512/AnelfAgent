"""PluginManager 变更钩子（on_change）测试。"""

import pytest

from core.plugins.manager import (
    ACTION_DISABLED,
    ACTION_ENABLED,
    ACTION_REMOVED,
    PluginManager,
)
from core.plugins.store import InstalledPlugin, PluginRegistry


@pytest.fixture()
def manager(tmp_path):
    """临时注册表管理器（免负载操作：toggle/remove 不依赖激活钩子）。"""
    return PluginManager(PluginRegistry(tmp_path / "config" / "plugins.json"))


class TestChangeHook:
    def test_toggle_notifies_both_directions(self, manager):
        manager.registry.upsert(InstalledPlugin(name="demo"))
        seen: list[tuple[str, str]] = []
        manager.on_change = lambda action, record: seen.append((action, record.name))
        manager.toggle("demo", False)
        manager.toggle("demo", True)
        assert seen == [(ACTION_DISABLED, "demo"), (ACTION_ENABLED, "demo")]

    def test_toggle_noop_not_notified(self, manager):
        manager.registry.upsert(InstalledPlugin(name="demo", enabled=False))
        seen: list[str] = []
        manager.on_change = lambda action, record: seen.append(action)
        manager.toggle("demo", False)
        assert seen == []

    def test_remove_notifies(self, manager):
        manager.registry.upsert(InstalledPlugin(name="demo"))
        seen: list[str] = []
        manager.on_change = lambda action, record: seen.append(action)
        manager.remove("demo")
        assert seen == [ACTION_REMOVED]
        assert manager.registry.get("demo") is None

    def test_hook_exception_swallowed(self, manager):
        manager.registry.upsert(InstalledPlugin(name="demo"))

        def boom(action, record):
            raise RuntimeError("hook crashed")

        manager.on_change = boom
        manager.remove("demo")  # 钩子异常不影响操作结果
        assert manager.registry.get("demo") is None

    def test_wiring_attaches_all_hooks(self, manager):
        from entities.plugins.activation import (
            activate_plugin,
            deactivate_plugin,
            notify_plugin_change,
            wire_plugin_manager,
        )

        wire_plugin_manager(manager)
        assert manager.on_activate is activate_plugin
        assert manager.on_deactivate is deactivate_plugin
        assert manager.on_change is notify_plugin_change
