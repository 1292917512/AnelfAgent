"""插件名册渲染、缓存与降级测试。"""

import pytest

from core.plugins.roster import roster_factor, roster_line, roster_section
from core.plugins.store import InstalledPlugin, PluginRegistry


@pytest.fixture()
def registry(tmp_path):
    """临时路径注册表实例。"""
    return PluginRegistry(tmp_path / "config" / "plugins.json")


class TestRosterSection:
    def test_empty_registry_renders_nothing(self, registry):
        assert roster_section(registry) == ""

    def test_full_line_components(self, registry):
        registry.upsert(InstalledPlugin(
            name="demo", version="0.9.0", marketplace="hub",
            description="演示插件", skills=["a", "b"],
            tools=["t1", "t2"], mcp_servers=["srv"],
        ))
        text = roster_section(registry)
        assert "[插件名册]" in text
        assert "demo v0.9.0（hub）" in text
        assert "技能: a、b" in text
        assert "工具: 2 个（组 plugin:demo）" in text
        assert "MCP: srv" in text
        assert "[已禁用]" not in text

    def test_disabled_flag_and_install_order(self, registry):
        registry.upsert(InstalledPlugin(name="first", installed_at=100))
        registry.upsert(InstalledPlugin(name="second", installed_at=200, enabled=False))
        text = roster_section(registry)
        assert text.index("first") < text.index("second")
        second_line = next(ln for ln in text.splitlines() if "second" in ln)
        assert second_line.endswith("[已禁用]")

    def test_version_cache(self, registry):
        registry.upsert(InstalledPlugin(name="demo"))
        first = roster_section(registry)
        assert roster_section(registry) == first
        registry.upsert(InstalledPlugin(name="another"))
        assert roster_section(registry) != first

    def test_instances_isolated(self, registry, tmp_path):
        registry.upsert(InstalledPlugin(name="demo"))
        other = PluginRegistry(tmp_path / "other" / "plugins.json")
        assert roster_section(registry) != ""
        assert roster_section(other) == ""

    def test_budget_downgrade_keeps_components(self, registry, monkeypatch):
        monkeypatch.setattr(
            "core.config.get_config_int",
            lambda key, default: 260 if key == "plugins_roster_max_chars" else default,
        )
        registry.upsert(InstalledPlugin(
            name="demo", version="1.0.0", description="长描述" * 40,
            skills=["s"], tools=["t"],
        ))
        registry.upsert(InstalledPlugin(name="overflow", description="x"))
        text = roster_section(registry)
        assert "未列出" in text
        demo_line = next(ln for ln in text.splitlines() if "demo" in ln)
        assert "组 plugin:demo" in demo_line  # 组件归属是名册核心价值，降级不丢
        assert "长描述" not in demo_line      # 描述先降级

    def test_disabled_switch_renders_nothing(self, registry, monkeypatch):
        registry.upsert(InstalledPlugin(name="demo"))
        monkeypatch.setattr(
            "core.config.get_config_bool",
            lambda key, default: False if key == "plugins_roster_enabled" else default,
        )
        assert roster_section(registry) == ""


class TestRosterLine:
    def test_truncates_description(self):
        line = roster_line(InstalledPlugin(name="d", description="长" * 200))
        assert "…" in line
        assert len(line) < 200

    def test_line_without_list_prefix(self):
        assert not roster_line(InstalledPlugin(name="d")).startswith("-")

    def test_factor_tracks_version(self, registry):
        first = roster_factor(registry)
        assert first.startswith("plugins-roster:")
        registry.upsert(InstalledPlugin(name="demo"))
        assert roster_factor(registry) != first
