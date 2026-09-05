"""插件清单与市场目录解析测试。"""

import json

import pytest

from core.plugins.manifest import (
    PluginError,
    find_manifest_file,
    load_plugin_mcp_servers,
    parse_manifest,
    parse_marketplace,
    validate_plugin_name,
)


class TestValidateName:
    def test_valid(self):
        assert validate_plugin_name("my-plugin.v2_beta") == "my-plugin.v2_beta"

    @pytest.mark.parametrize("bad", ["", "UPPER", "1 2", "-lead", "a" * 65, "中文"])
    def test_invalid(self, bad):
        with pytest.raises(PluginError):
            validate_plugin_name(bad)


class TestManifestDiscovery:
    def test_root_manifest(self, tmp_path):
        (tmp_path / "plugin.json").write_text("{}", encoding="utf-8")
        assert find_manifest_file(tmp_path) == tmp_path / "plugin.json"

    def test_dotdir_fallback(self, tmp_path):
        d = tmp_path / ".codex-plugin"
        d.mkdir()
        (d / "plugin.json").write_text("{}", encoding="utf-8")
        assert find_manifest_file(tmp_path) == d / "plugin.json"

    def test_root_preferred_over_dotdir(self, tmp_path):
        (tmp_path / "plugin.json").write_text("{}", encoding="utf-8")
        d = tmp_path / ".codex-plugin"
        d.mkdir()
        (d / "plugin.json").write_text("{}", encoding="utf-8")
        assert find_manifest_file(tmp_path) == tmp_path / "plugin.json"

    def test_missing(self, tmp_path):
        assert find_manifest_file(tmp_path) is None
        with pytest.raises(PluginError):
            parse_manifest(tmp_path)


class TestParseManifest:
    def test_full_fields(self, tmp_path):
        (tmp_path / "skills").mkdir()
        (tmp_path / "tools.py").write_text("# t", encoding="utf-8")
        (tmp_path / "plugin.json").write_text(json.dumps({
            "name": "demo",
            "version": "2.1.0",
            "description": "d",
            "author": {"name": "alice"},
            "keywords": ["a", "b"],
            "interface": {"displayName": "Demo", "category": "生产力",
                          "defaultPrompt": ["x", "y", "z", "w"]},
        }), encoding="utf-8")
        m = parse_manifest(tmp_path)
        assert m.name == "demo"
        assert m.version == "2.1.0"
        assert m.author == "alice"
        assert m.keywords == ["a", "b"]
        assert m.display_name == "Demo"
        assert len(m.interface.default_prompt) == 3  # 上限 3 条
        assert m.skills == ["./skills"]
        assert m.tools_file == "./tools.py"

    def test_name_fallback_to_dir(self, tmp_path):
        (tmp_path / "plugin.json").write_text("{}", encoding="utf-8")
        assert parse_manifest(tmp_path).name == tmp_path.name

    def test_invalid_json(self, tmp_path):
        (tmp_path / "plugin.json").write_text("{bad", encoding="utf-8")
        with pytest.raises(PluginError):
            parse_manifest(tmp_path)

    def test_mcp_inline(self, tmp_path):
        (tmp_path / "plugin.json").write_text(json.dumps({
            "name": "demo",
            "mcpServers": {"mcpServers": {"s1": {"url": "http://x"}}},
        }), encoding="utf-8")
        m = parse_manifest(tmp_path)
        assert m.mcp_servers_inline == {"s1": {"url": "http://x"}}

    def test_mcp_file_discovery(self, tmp_path):
        (tmp_path / "plugin.json").write_text('{"name": "demo"}', encoding="utf-8")
        (tmp_path / ".mcp.json").write_text(
            '{"mcpServers": {"s1": {"command": "npx"}}}', encoding="utf-8")
        m = parse_manifest(tmp_path)
        assert m.mcp_servers_file == ".mcp.json"
        servers = load_plugin_mcp_servers(tmp_path, m)
        assert servers == {"s1": {"command": "npx"}}

    def test_component_path_escape_rejected(self, tmp_path):
        (tmp_path / "plugin.json").write_text(json.dumps({
            "name": "demo", "skills": "../outside",
        }), encoding="utf-8")
        with pytest.raises(PluginError):
            parse_manifest(tmp_path)


class TestParseMarketplace:
    def test_full(self):
        m = parse_marketplace({
            "name": "official",
            "interface": {"displayName": "官方"},
            "plugins": [
                {"name": "a", "source": {"source": "local", "path": "./plugins/a"},
                 "category": "工具", "policy": {"installation": "AVAILABLE"}},
                {"name": "b", "source": {"source": "git", "url": "https://x/y.git",
                                         "ref": "main"}},
            ],
        })
        assert m.name == "official"
        assert m.display_name == "官方"
        assert len(m.plugins) == 2
        assert m.find("a").source_type == "local"
        assert m.find("b").url == "https://x/y.git"
        assert m.find("missing") is None

    def test_missing_name(self):
        with pytest.raises(PluginError):
            parse_marketplace({"plugins": []})

    def test_not_dict(self):
        with pytest.raises(PluginError):
            parse_marketplace([])


class TestEntrySourceShapes:
    """市场条目 source 的多种声明形态。"""

    def test_string_shorthand_local(self):
        m = parse_marketplace({"name": "m", "plugins": [
            {"name": "a", "source": "./plugins/a"}]})
        entry = m.find("a")
        assert entry.source_type == "local"
        assert entry.path == "./plugins/a"

    def test_url_kind_git(self):
        m = parse_marketplace({"name": "m", "plugins": [
            {"name": "a", "source": {"source": "url",
                                     "url": "https://x/y.git",
                                     "sha": "abc123"}}]})
        entry = m.find("a")
        assert entry.source_type == "git"
        assert entry.url == "https://x/y.git"
        assert entry.ref == "abc123"

    def test_git_subdir_kind(self):
        m = parse_marketplace({"name": "m", "plugins": [
            {"name": "a", "source": {"source": "git-subdir",
                                     "url": "https://x/mono.git",
                                     "path": "plugins/a", "ref": "v1.0"}}]})
        entry = m.find("a")
        assert entry.source_type == "git"
        assert entry.subdir == "plugins/a"
        assert entry.ref == "v1.0"

    def test_github_kind(self):
        m = parse_marketplace({"name": "m", "plugins": [
            {"name": "a", "source": {"source": "github", "repo": "owner/repo"}}]})
        entry = m.find("a")
        assert entry.url == "https://github.com/owner/repo.git"

    def test_git_without_url_skipped(self):
        m = parse_marketplace({"name": "m", "plugins": [
            {"name": "bad", "source": {"source": "git"}},
            {"name": "ok", "source": "./plugins/ok"}]})
        assert m.find("bad") is None
        assert m.find("ok") is not None
