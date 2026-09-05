"""插件管理器编排测试（安装/升级/移除/启停/市场，本地源为主）。"""

import json

import pytest

from core.plugins.manifest import PluginError
from core.plugins.store import plugin_payload_dir


class TestInstallFromSource:
    def test_install_local(self, manager, plugin_env):
        pkg = plugin_env.make_plugin("demo")
        record = manager.install_from_source(str(pkg))
        assert record.name == "demo"
        assert record.version == "1.0.0"
        assert record.sha  # 本地源有内容指纹
        assert plugin_payload_dir("demo").is_dir()
        # 组件已激活并回写记录
        assert record.skills == ["demo_skill"]
        assert record.tools == ["demo_ping"]
        assert record.mcp_servers == ["demo_srv"]

    def test_install_twice_rejected(self, manager, plugin_env):
        pkg = plugin_env.make_plugin("demo")
        manager.install_from_source(str(pkg))
        with pytest.raises(PluginError, match="已安装"):
            manager.install_from_source(str(pkg))

    def test_missing_manifest_rejected(self, manager, tmp_path):
        bare = tmp_path / "bare"
        bare.mkdir()
        with pytest.raises(PluginError, match="清单"):
            manager.install_from_source(str(bare))

    def test_missing_path_rejected(self, manager):
        with pytest.raises(PluginError, match="不存在"):
            manager.install_from_source("/nonexistent/path/xyz")

    def test_dotdir_format_manifest(self, manager, plugin_env):
        pkg = plugin_env.make_plugin("legacy", manifest_format="dotdir")
        record = manager.install_from_source(str(pkg))
        assert record.name == "legacy"


class TestUpgrade:
    def test_no_change(self, manager, plugin_env):
        pkg = plugin_env.make_plugin("demo")
        manager.install_from_source(str(pkg))
        _, changed = manager.upgrade("demo")
        assert changed is False

    def test_version_change(self, manager, plugin_env):
        pkg = plugin_env.make_plugin("demo")
        manager.install_from_source(str(pkg))
        manifest = json.loads((pkg / "plugin.json").read_text(encoding="utf-8"))
        manifest["version"] = "2.0.0"
        (pkg / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        record, changed = manager.upgrade("demo")
        assert changed is True
        assert record.version == "2.0.0"
        # 升级后组件仍在
        assert record.tools == ["demo_ping"]

    def test_upgrade_not_installed(self, manager):
        with pytest.raises(PluginError, match="未安装"):
            manager.upgrade("ghost")


class TestRemove:
    def test_remove_recycles(self, manager, plugin_env):
        from core.entity import EntityRegistry

        pkg = plugin_env.make_plugin("demo")
        manager.install_from_source(str(pkg))
        manager.remove("demo")
        assert manager.get_plugin("demo") is None
        assert not plugin_payload_dir("demo").exists()
        assert EntityRegistry.get("demo_ping") is None
        assert EntityRegistry.get("plugin:demo") is None
        assert not (plugin_env.skills_dir / "demo_skill").exists()
        assert "demo_srv" not in plugin_env.read_mcp_servers()

    def test_remove_not_installed(self, manager):
        with pytest.raises(PluginError, match="未安装"):
            manager.remove("ghost")


class TestToggle:
    def test_disable_and_enable(self, manager, plugin_env):
        from core.entity import EntityRegistry

        pkg = plugin_env.make_plugin("demo")
        manager.install_from_source(str(pkg))
        record = manager.toggle("demo", False)
        assert record.enabled is False
        assert EntityRegistry.get("demo_ping") is None
        assert "demo_srv" not in plugin_env.read_mcp_servers()
        record = manager.toggle("demo", True)
        assert record.enabled is True
        assert EntityRegistry.get("demo_ping") is not None
        assert "demo_srv" in plugin_env.read_mcp_servers()


class TestMarketplace:
    def _make_marketplace(self, plugin_env, plugins=("alpha", "beta")) -> str:
        root = plugin_env.root / "market"
        (root / ".agents" / "plugins").mkdir(parents=True)
        entries = []
        for name in plugins:
            pkg = plugin_env.make_plugin(name)
            # 市场内插件放在 <root>/plugins/<name>
            target = root / "plugins" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copytree(pkg, target)
            entries.append({
                "name": name,
                "source": {"source": "local", "path": f"./plugins/{name}"},
                "category": "工具",
                "policy": {"installation": "AVAILABLE"},
            })
        (root / ".agents" / "plugins" / "marketplace.json").write_text(
            json.dumps({"name": "local-mart", "plugins": entries}), encoding="utf-8")
        return str(root)

    def test_add_and_search(self, manager, plugin_env):
        root = self._make_marketplace(plugin_env)
        manager.add_marketplace("mart", root)
        markets = manager.list_marketplaces()
        assert markets[0]["name"] == "mart"
        assert markets[0]["plugin_count"] == 2
        results = manager.search("alpha")
        assert len(results) == 1
        assert results[0]["name"] == "alpha"
        assert results[0]["installed"] is False

    def test_install_from_marketplace(self, manager, plugin_env):
        root = self._make_marketplace(plugin_env)
        manager.add_marketplace("mart", root)
        record = manager.install("alpha")
        assert record.name == "alpha"
        assert record.marketplace == "mart"
        assert manager.search("alpha")[0]["installed"] is True

    def test_remove_marketplace_with_dependents_rejected(self, manager, plugin_env):
        root = self._make_marketplace(plugin_env)
        manager.add_marketplace("mart", root)
        manager.install("alpha")
        with pytest.raises(PluginError, match="仍有已安装插件"):
            manager.remove_marketplace("mart")

    def test_install_missing_entry(self, manager, plugin_env):
        root = self._make_marketplace(plugin_env)
        manager.add_marketplace("mart", root)
        with pytest.raises(PluginError, match="未找到插件"):
            manager.install("ghost")

    def test_add_twice_rejected(self, manager, plugin_env):
        root = self._make_marketplace(plugin_env)
        manager.add_marketplace("mart", root)
        with pytest.raises(PluginError, match="已订阅"):
            manager.add_marketplace("mart", root)

    def test_refresh_local(self, manager, plugin_env):
        root = self._make_marketplace(plugin_env)
        manager.add_marketplace("mart", root)
        results = manager.refresh_marketplaces("mart")
        assert results == {"mart": 2}


def _git(args, cwd):
    import subprocess
    subprocess.run(["git", *args], cwd=str(cwd), check=True,
                   capture_output=True, env={"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                                             "HOME": str(cwd)})


class TestGitSource:
    @pytest.fixture()
    def git_repo(self, plugin_env, tmp_path):
        """本地 git 仓库形式的插件包。"""
        import shutil
        pkg = plugin_env.make_plugin("gitplug")
        repo = tmp_path / "gitrepo"
        shutil.copytree(pkg, repo)
        _git(["init", "-q"], repo)
        _git(["add", "-A"], repo)
        _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "v1"], repo)
        return repo

    def test_install_from_git(self, manager, git_repo):
        record = manager.install_from_source(f"file://{git_repo}")
        assert record.name == "gitplug"
        assert record.source_type == "git"
        assert len(record.sha) == 40
        assert record.tools == ["gitplug_ping"]

    def test_upgrade_detects_new_commit(self, manager, git_repo):
        manager.install_from_source(f"file://{git_repo}")
        _, changed = manager.upgrade("gitplug")
        assert changed is False
        # 新提交后升级生效
        import json
        manifest = json.loads((git_repo / "plugin.json").read_text(encoding="utf-8"))
        manifest["version"] = "2.0.0"
        (git_repo / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        _git(["add", "-A"], git_repo)
        _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "v2"], git_repo)
        record, changed = manager.upgrade("gitplug")
        assert changed is True
        assert record.version == "2.0.0"

    def test_git_marketplace(self, manager, git_repo, plugin_env, tmp_path):
        import json
        mart_repo = tmp_path / "martrepo"
        mart_repo.mkdir()
        (mart_repo / "marketplace.json").write_text(json.dumps({
            "name": "git-mart",
            "plugins": [{
                "name": "gitplug",
                "source": {"source": "git", "url": f"file://{git_repo}"},
            }],
        }), encoding="utf-8")
        _git(["init", "-q"], mart_repo)
        _git(["add", "-A"], mart_repo)
        _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "m1"], mart_repo)
        manager.add_marketplace("gmart", f"file://{mart_repo}")
        assert manager.list_marketplaces()[0]["plugin_count"] == 1
        record = manager.install("gitplug")
        assert record.marketplace == "gmart"
        # 刷新（pull 无变更也正常返回条目数）
        assert manager.refresh_marketplaces("gmart") == {"gmart": 1}


class TestNameFallback:
    def test_manifest_without_name_uses_dir(self, manager, plugin_env):
        """清单缺省 name 字段时按插件目录名回退。"""
        pkg = plugin_env.make_plugin("dirnamed", skill=False, mcp=False, tools=False)
        (pkg / "plugin.json").write_text('{"version": "0.1.0"}', encoding="utf-8")
        record = manager.install_from_source(str(pkg))
        assert record.name == "dirnamed"


class TestMarketplaceFileDiscovery:
    def test_dotdir_marketplace(self, manager, plugin_env):
        """marketplace.json 位于 .claude-plugin/ 下时以仓库根为条目路径基准。"""
        root = plugin_env.root / "mart_dot"
        (root / ".claude-plugin").mkdir(parents=True)
        pkg = plugin_env.make_plugin("dotplug")
        import shutil
        target = root / "plugins" / "dotplug"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(pkg, target)
        (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
            "name": "dot-mart",
            "plugins": [{"name": "dotplug", "source": "./plugins/dotplug"}],
        }), encoding="utf-8")
        manager.add_marketplace("dotmart", str(root))
        assert manager.list_marketplaces()[0]["plugin_count"] == 1
        record = manager.install("dotplug")
        assert record.name == "dotplug"
