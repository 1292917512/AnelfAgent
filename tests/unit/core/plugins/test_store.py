"""插件注册表持久化测试。"""

import pytest

from core.plugins.store import InstalledPlugin, MarketplaceSource, PluginRegistry


@pytest.fixture()
def registry(tmp_path):
    """临时路径注册表实例。"""
    return PluginRegistry(tmp_path / "config" / "plugins.json")


class TestPluginRegistry:
    def test_upsert_and_reload(self, registry):
        registry.upsert(InstalledPlugin(
            name="demo", version="1.0.0", skills=["s1"], tools=["t1"],
        ))
        # 新实例从磁盘读到同一份记录
        fresh = PluginRegistry(registry._path)
        record = fresh.get("demo")
        assert record is not None
        assert record.version == "1.0.0"
        assert record.skills == ["s1"]
        assert record.installed_at > 0

    def test_remove(self, registry):
        registry.upsert(InstalledPlugin(name="demo"))
        removed = registry.remove("demo")
        assert removed is not None
        assert registry.get("demo") is None
        assert registry.remove("demo") is None

    def test_list_sorted(self, registry):
        registry.upsert(InstalledPlugin(name="b"))
        registry.upsert(InstalledPlugin(name="a"))
        assert [p.name for p in registry.list_installed()] == ["a", "b"]

    def test_corrupt_file_tolerated(self, registry):
        registry._path.parent.mkdir(parents=True, exist_ok=True)
        registry._path.write_text("{bad json", encoding="utf-8")
        registry.reload()
        assert registry.list_installed() == []

    def test_unknown_fields_ignored(self, registry):
        registry._path.parent.mkdir(parents=True, exist_ok=True)
        registry._path.write_text(
            '{"installed": {"demo": {"name": "demo", "future_field": 1}}}',
            encoding="utf-8")
        registry.reload()
        assert registry.get("demo").name == "demo"


class TestMarketplaceRegistry:
    def test_marketplace_crud(self, registry):
        registry.upsert_marketplace(MarketplaceSource(
            name="official", source_type="git", url="https://x/y.git"))
        fresh = PluginRegistry(registry._path)
        assert fresh.get_marketplace("official").url == "https://x/y.git"
        assert [m.name for m in fresh.list_marketplaces()] == ["official"]
        assert fresh.remove_marketplace("official") is not None
        assert fresh.get_marketplace("official") is None
