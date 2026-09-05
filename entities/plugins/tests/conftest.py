"""plugins 测试共享 fixture：路径隔离 + 插件包工广 + 实体注册表清理。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def plugin_env(tmp_path, monkeypatch):
    """隔离插件注册表 / 负载目录 / MCP 配置 / 技能库目录，返回路径命名空间。"""
    from core.path import ConfigPaths

    monkeypatch.setattr(ConfigPaths, "PLUGINS_DIR", str(tmp_path / "workspace" / "plugins"))
    monkeypatch.setattr(ConfigPaths, "PLUGINS_REGISTRY", str(tmp_path / "config" / "plugins.json"))
    monkeypatch.setenv("ANELF_MCP_CONFIG", str(tmp_path / "config" / "mcp_servers.json"))
    monkeypatch.setenv("ANELF_MCP_CONFIG_PATH", "")
    monkeypatch.setattr(
        "entities.plugins.activation.workspace_root",
        lambda: str(tmp_path / "workspace"),
    )

    class Env:
        root = tmp_path
        skills_dir = tmp_path / "workspace" / "skills"
        plugins_dir = tmp_path / "workspace" / "plugins"
        mcp_config = tmp_path / "config" / "mcp_servers.json"

        @staticmethod
        def make_plugin(name: str, version: str = "1.0.0", *, skill: bool = True,
                        mcp: bool = True, tools: bool = True,
                        manifest_format: str = "root") -> Path:
            """构造一个本地插件包目录。"""
            pkg = tmp_path / "pkgs" / name
            pkg.mkdir(parents=True, exist_ok=True)
            manifest = {
                "name": name,
                "version": version,
                "description": f"{name} plugin",
                "interface": {"displayName": name.title()},
            }
            if manifest_format == "root":
                (pkg / "plugin.json").write_text(
                    json.dumps(manifest), encoding="utf-8")
            else:
                (pkg / ".codex-plugin").mkdir(exist_ok=True)
                (pkg / ".codex-plugin" / "plugin.json").write_text(
                    json.dumps(manifest), encoding="utf-8")
            if skill:
                skill_dir = pkg / "skills" / f"{name}_skill"
                skill_dir.mkdir(parents=True, exist_ok=True)
                (skill_dir / "SKILL.md").write_text(
                    f"---\nname: {name}_skill\ndescription: s\n---\nbody\n",
                    encoding="utf-8")
            if mcp:
                (pkg / ".mcp.json").write_text(json.dumps({
                    "mcpServers": {
                        f"{name}_srv": {
                            "url": "http://127.0.0.1:19999/sse",
                            "enabled": False,
                        },
                    },
                }), encoding="utf-8")
            if tools:
                (pkg / "tools.py").write_text(
                    "from entities._sdk import tool\n\n"
                    f"@tool(name=\"{name}_ping\", group=\"{name}_grp\")\n"
                    f"def {name}_ping() -> str:\n"
                    f"    \"\"\"ping\"\"\"\n"
                    f"    return \"pong\"\n",
                    encoding="utf-8")
            return pkg

        def read_mcp_servers(self) -> dict:
            if not self.mcp_config.exists():
                return {}
            return json.loads(self.mcp_config.read_text(encoding="utf-8")).get("mcpServers", {})

    return Env()


@pytest.fixture()
def registry(plugin_env):
    """隔离注册表实例。"""
    from core.plugins.store import PluginRegistry

    return PluginRegistry()


@pytest.fixture()
def manager(registry):
    """接线激活钩子的插件管理器（用例结束清理注册的实体）。"""
    from core.plugins.manager import PluginManager
    from entities.plugins.activation import wire_plugin_manager

    mgr = PluginManager(registry)
    wire_plugin_manager(mgr)
    yield mgr
    for record in list(mgr.list_plugins()):
        try:
            mgr.remove(record.name)
        except Exception:
            pass
