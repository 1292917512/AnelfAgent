"""插件管理实体工具测试（AI 自主管理面）。"""

import json

import pytest

from entities.plugins import tools as plugin_tools


@pytest.fixture()
def svc(manager, monkeypatch):
    """把实体工具指向隔离的插件管理器。"""
    monkeypatch.setattr(plugin_tools, "get_plugin_manager", lambda: manager)
    return plugin_tools


class TestPluginTools:
    async def test_list_empty(self, svc):
        assert json.loads(svc.list_plugins()) == []

    async def test_install_and_list(self, svc, plugin_env):
        pkg = plugin_env.make_plugin("demo")
        result = json.loads(await svc.install_plugin_from_source(str(pkg)))
        assert result["installed"] is True
        assert result["name"] == "demo"

        records = json.loads(svc.list_plugins())
        assert records[0]["name"] == "demo"
        assert records[0]["tools"] == ["demo_ping"]

    async def test_info_not_found(self, svc):
        result = json.loads(svc.plugin_info("ghost"))
        assert "error" in result
        assert result["cause"] == "not_found"

    async def test_toggle(self, svc, plugin_env):
        pkg = plugin_env.make_plugin("demo")
        await svc.install_plugin_from_source(str(pkg))
        result = json.loads(await svc.toggle_plugin("demo", False))
        assert result["enabled"] is False
        result = json.loads(await svc.toggle_plugin("demo", True))
        assert result["enabled"] is True

    async def test_remove(self, svc, plugin_env):
        pkg = plugin_env.make_plugin("demo")
        await svc.install_plugin_from_source(str(pkg))
        result = json.loads(await svc.remove_plugin("demo"))
        assert result["removed"] is True
        assert json.loads(svc.list_plugins()) == []

    async def test_upgrade(self, svc, plugin_env):
        pkg = plugin_env.make_plugin("demo")
        await svc.install_plugin_from_source(str(pkg))
        result = json.loads(await svc.upgrade_plugins("demo"))
        assert result["upgraded"] is False
        result = json.loads(await svc.upgrade_plugins(""))
        assert result["results"] == {"demo": False}


class TestMarketplaceTools:
    async def test_add_list_remove(self, svc, plugin_env):
        root = plugin_env.root / "mart"
        (root / ".agents" / "plugins").mkdir(parents=True)
        (root / ".agents" / "plugins" / "marketplace.json").write_text(
            '{"name": "m", "plugins": []}', encoding="utf-8")
        result = json.loads(await svc.add_marketplace("mart", str(root)))
        assert result["subscribed"] is True

        markets = json.loads(svc.list_marketplaces())
        assert markets[0]["name"] == "mart"
        assert markets[0]["plugin_count"] == 0

        result = json.loads(svc.remove_marketplace("mart"))
        assert result["removed"] is True

    async def test_search_empty(self, svc):
        result = json.loads(svc.search_plugins("x"))
        assert result["count"] == 0

    async def test_error_attribution(self, svc):
        """非法操作返回归因明确的工具错误而非异常。"""
        result = json.loads(await svc.install_plugin("ghost"))
        assert "error" in result
        assert "cause" in result


class TestOperationStatusProvider:
    async def test_empty_when_idle(self, svc, monkeypatch):
        """无进行中操作与失败时返回 None（零占用）。"""
        from core.plugins.status import OperationBoard
        monkeypatch.setattr("core.plugins.status._board", OperationBoard())
        assert await svc.plugin_operation_status("") is None

    async def test_reports_active_and_failure(self, svc, monkeypatch):
        """有进行中操作或最近失败时注入一行状态。"""
        from core.plugins.status import OperationBoard
        board = OperationBoard()
        monkeypatch.setattr("core.plugins.status._board", board)
        board.record_failure("升级", "demo", "git clone 超时")
        text = await svc.plugin_operation_status("")
        assert "最近失败" in text and "demo" in text and "git clone 超时" in text

        with board.track("安装", "newplug"):
            text = await svc.plugin_operation_status("")
            assert "进行中" in text and "安装 newplug" in text
        assert "进行中" not in (await svc.plugin_operation_status("") or "")
