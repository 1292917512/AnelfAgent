"""公共插件接口的 DSH 格式识别、生命周期与错误恢复。"""

import json
from pathlib import Path

import pytest

from core.plugins.manifest import PluginError, parse_manifest
from core.plugins.store import plugin_payload_dir


def bundle(root: Path, patch: str) -> Path:
    root.mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({
        "name": "@demo/draw", "version": "1.0.0", "dsh": {"bundle": {"patch": "./cordis.yml"}},
    }), encoding="utf-8")
    (root / "cordis.yml").write_text(patch, encoding="utf-8")
    return root


MCP = """- insert:
    - id: draw
      name: '@deepseek-ai/dsh-mcp-client'
      config: {serverName: draw, transport: stdio, command: draw, args: [serve], toolCallTimeoutMs: 120000}
- id: ui-sidebar-browser
  disabled: false
"""


@pytest.mark.parametrize(("patch", "format"), [(MCP, "dsh"), ("- insert: [{name: custom-plugin}]", "dsh-host")])
def test_inspect_dsh_without_host_or_registration(manager, plugin_env, patch, format):
    root = bundle(plugin_env.root / "inspect", patch)
    manifest = root / "package.json"
    data = json.loads(manifest.read_text(encoding="utf8"))
    data["engines"] = {"dsh": "^0.2.0"}
    manifest.write_text(json.dumps(data), encoding="utf8")
    result = manager.inspect(source=str(root))
    assert (result["status"], result["format"], result["requirements"]) == ("verified", format, {"dsh": "^0.2.0"})
    assert result["mcp_servers"] == (["draw"] if format == "dsh" else [])
    assert not manager.list_plugins() and not plugin_env.read_mcp_servers()


def test_mcp_bundle_lifecycle_and_disabled_upgrade(manager, plugin_env):
    repo = plugin_env.root / "repo"
    root = bundle(repo / "packages" / "draw", MCP)
    record = manager.install_from_source(str(repo), subdir="packages/draw")
    assert (record.name, record.display_name, record.format) == ("demo--draw", "@demo/draw", "dsh")
    assert plugin_env.read_mcp_servers()["draw"]["call_timeout"] == 120
    manager.toggle(record.name, False)
    (root / "cordis.yml").write_text(MCP.replace("command: draw", "command: new-draw"), encoding="utf-8")
    assert manager.upgrade(record.name)[1]
    assert "draw" not in plugin_env.read_mcp_servers()
    manager.toggle(record.name, True)
    assert plugin_env.read_mcp_servers()["draw"]["command"] == "new-draw"
    manager.remove(record.name)
    assert not plugin_payload_dir(record.name).exists()
    assert "draw" not in plugin_env.read_mcp_servers()


def test_failed_upgrade_restores_payload_and_registration(manager, plugin_env, monkeypatch):
    root = bundle(plugin_env.root / "sample", MCP)
    record = manager.install_from_source(str(root))
    (root / "cordis.yml").write_text(MCP.replace("command: draw", "command: broken"), encoding="utf-8")
    activate = manager.on_activate

    def activate_candidate(candidate, directory):
        result = activate(candidate, directory)
        if result.sha != record.sha:
            raise PluginError("activation failed")
        return result

    monkeypatch.setattr(manager, "on_activate", activate_candidate)
    errors: dict[str, str] = {}
    assert manager.upgrade_all(errors=errors) == {record.name: False}
    assert errors == {record.name: "activation failed"}
    assert manager.get_plugin(record.name).sha == record.sha
    assert (plugin_payload_dir(record.name) / "cordis.yml").read_text(encoding="utf-8") == MCP
    assert plugin_env.read_mcp_servers()["draw"]["command"] == "draw"


def test_precedence_host_and_path_boundary(tmp_path: Path) -> None:
    root = bundle(tmp_path / "draw", "- insert: [{id: draw, name: '@demo/draw'}]")
    assert parse_manifest(root).format == "dsh-host"
    data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    data["dsh"]["bundle"]["patch"] = ["cordis.yml", "../outside.yml"]
    (root / "package.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PluginError, match="越出"):
        parse_manifest(root)
    (root / "plugin.json").write_text('{"name":"legacy"}', encoding="utf-8")
    assert parse_manifest(root).name == "legacy"


def test_topic_market_selection_and_monorepo_install(manager, plugin_env, monkeypatch):
    import httpx

    from core.plugins import github_topic

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "items": [{"full_name": "Example/Draw", "description": "diagram editor"}],
    }))
    monkeypatch.setattr(github_topic, "network_client", lambda **_: httpx.Client(transport=transport))
    manager.add_marketplace("community", "https://github.com/topics/dsh-plugin")
    with pytest.raises(PluginError, match="未订阅"):
        manager.search("diagram", "different")
    assert manager.search("diagram", "community")[0]["name"] == "Example/Draw"

    def fetch(url, ref, staging, *, subdir):
        assert url == "https://github.com/Example/Draw.git"
        root = staging / "repo"
        bundle(root / "packages" / "draw", MCP)
        return root, "commit"

    monkeypatch.setattr("core.plugins.manager.fetch_git", fetch)
    record = manager.install("Example/Draw", "community")
    assert record.subdir == "packages/draw"
    assert manager.search("", "community")[0]["installed"]
    manager.remove(record.name)
    manager.remove_marketplace("community")
    assert manager.list_marketplaces() == []


def test_remove_persistence_failure_restores_plugin(manager, plugin_env, monkeypatch):
    record = manager.install_from_source(str(bundle(plugin_env.root / "package", MCP)))

    def disk_error():
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(manager.registry, "_save", disk_error)
        with pytest.raises(OSError, match="disk full"):
            manager.remove(record.name)
    assert manager.get_plugin(record.name).enabled
    assert plugin_payload_dir(record.name).is_dir()
    assert plugin_env.read_mcp_servers()["draw"]["plugin"] == record.name


def test_host_profile_lifecycle_preserves_settings(manager, plugin_env, monkeypatch):
    from core.path import ConfigPaths
    from core.plugins import dsh_host

    monkeypatch.chdir(plugin_env.root)
    monkeypatch.setattr(ConfigPaths, "PLUGINS_DIR", "workspace/plugins")

    root = bundle(plugin_env.root / "package", "- insert: [{id: draw, name: '@demo/draw'}]")
    calls = []

    def official_cli(home, *args):
        calls.append(args)
        manifest = home / "profiles" / "web" / "package.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        bundles = ["@demo/draw"] if "add" in args else []
        if "add" in args or "remove" in args or not manifest.exists():
            manifest.write_text(json.dumps({"dsh": {"profile": {"bundles": bundles}}}), encoding="utf-8")

    monkeypatch.setattr(dsh_host, "_run", official_cli)
    record = manager.install_from_source(str(root))
    home = Path(record.dsh_home)
    assert home.is_absolute()
    marker = home / "settings.json"
    marker.write_text("{}", encoding="utf-8")
    assert record.format == "dsh-host" and any("add" in call for call in calls)
    manager.toggle(record.name, False)
    assert any("remove" in call for call in calls) and marker.is_file()
    manager.toggle(record.name, True)
    assert sum("add" in call for call in calls) == 2
    manager.remove(record.name)
    assert not home.exists()


def test_saved_relative_dsh_home_uses_existing_profile(plugin_env, monkeypatch):
    from core.path import ConfigPaths
    from core.plugins.store import InstalledPlugin, dsh_profile_home, plugin_host_dir

    monkeypatch.chdir(plugin_env.root)
    monkeypatch.setattr(ConfigPaths, "PLUGINS_DIR", "workspace/plugins")
    relative = "workspace/plugins/_dsh/demo"
    nested = plugin_host_dir("demo") / relative
    manifest = nested / "profiles/web/package.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}", encoding="utf-8")
    record = InstalledPlugin.from_dict({"name": "demo", "format": "dsh-host", "dsh_home": relative})
    assert Path(record.dsh_home) == nested
    assert dsh_profile_home("demo", record.dsh_home) == nested
    assert dsh_profile_home("demo", "../some-other-plugin") == plugin_host_dir("demo")
