"""插件变更通知桥测试（仅非 AI 发起的操作推送告知 AI）。"""

import pytest

from core.plugins.store import InstalledPlugin
from entities.plugins import activation


@pytest.fixture()
def pushed(monkeypatch):
    """默认模拟 Web/外部路径（无会话 scope），返回推送收集列表。"""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr("entities._sdk.get_current_scope", lambda: "_global")
    monkeypatch.setattr(
        "entities._sdk.push_notify",
        lambda content, source, **kw: calls.append((source, content)) or True,
    )
    return calls


class TestNotifyPluginChange:
    def test_global_scope_pushes_with_roster_line(self, pushed):
        record = InstalledPlugin(
            name="demo", version="1.0.0", marketplace="hub",
            skills=["s"], tools=["t1", "t2"],
        )
        activation.notify_plugin_change("installed", record)
        assert len(pushed) == 1
        source, content = pushed[0]
        assert source == "plugins"
        assert "demo" in content and "已安装" in content
        assert "技能: s" in content and "组 plugin:demo" in content

    def test_session_scope_skipped(self, pushed, monkeypatch):
        monkeypatch.setattr("entities._sdk.get_current_scope", lambda: "user_qq:1")
        activation.notify_plugin_change("removed", InstalledPlugin(name="demo"))
        assert pushed == []

    def test_unknown_action_uses_raw_verb(self, pushed):
        activation.notify_plugin_change("weird", InstalledPlugin(name="demo"))
        assert "weird" in pushed[0][1]


def test_list_plugins_registered_as_always_entry():
    """插件组的发现入口常驻 schema（对齐 list_entity_methods/list_skills 范式）。"""
    import entities.plugins.tools  # noqa: F401  # 触发注册
    from core.entity import EntityRegistry

    entry = EntityRegistry.get("list_plugins")
    assert entry is not None
    assert "always" in entry.tags
