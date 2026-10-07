"""Minecraft 安装升级沿用连接参数，显式选项才覆盖已保存值。"""

import argparse
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from core.plugins.manager import PluginManager
from entities.mcp.config import MCPServerStore
from scripts import setup_minecraft


@pytest.mark.parametrize("upgrade", [False, True])
def test_setup_preserves_connection_and_player_settings(
    manager: PluginManager,
    plugin_env: Any,
    monkeypatch: pytest.MonkeyPatch,
    upgrade: bool,
) -> None:
    pkg = plugin_env.make_plugin("minecraft-companion", tools=False, skill=False)
    manager.install_from_source(str(pkg))
    name = "minecraft-companion_srv"
    store = MCPServerStore()
    store.update_server_config(
        name,
        {
            "env": {
                "MCP_DEFAULT_HOST": "192.168.1.10",
                "MCP_DEFAULT_PORT": "54321",
                "MCP_DEFAULT_USERNAME": "bot@example.com",
                "MCP_DEFAULT_AUTH": "microsoft",
                "MCP_DEFAULT_VERSION": "26.1",
                "MCP_DISABLE_GROUPS": "raw",
            }
        },
    )
    (pkg / "change.txt").write_text("upgrade payload", encoding="utf-8")
    saved = {"minecraft_server_id": "friends", "minecraft_allowed_players": ["Alice"]}
    write_channel = Mock()
    monkeypatch.setattr("core.config.ConfigManager.initialize", lambda: None)
    monkeypatch.setattr("core.config.ConfigManager.get", lambda key, default=None: saved.get(key, default))
    monkeypatch.setattr("core.path.workspace_root", lambda: str(plugin_env.root / "workspace"))
    monkeypatch.setattr("core.plugins.get_plugin_manager", lambda: manager)
    monkeypatch.setattr("agent.channel.config.register_channel_schema", lambda name: True)
    monkeypatch.setattr("agent.channel.config.set_channel_config", write_channel)
    monkeypatch.setattr(setup_minecraft, "node_runtime", lambda *args: (Path("node"), Path("npm-cli.js")))
    monkeypatch.setattr(setup_minecraft.subprocess, "run", Mock())
    args = argparse.Namespace(
        host=None,
        port=54322,
        username=None,
        auth=None,
        version=None,
        world_id=None,
        player=None,
        clear_players=False,
        node=None,
        npm_cli="",
        download_node=False,
        upgrade=upgrade,
    )

    setup_minecraft.setup(args)

    current = store.get_server_config(name)
    assert current is not None
    assert current["env"]["MCP_DEFAULT_HOST"] == "192.168.1.10"
    assert current["env"]["MCP_DEFAULT_PORT"] == "54322"
    assert current["env"]["MCP_DEFAULT_USERNAME"] == "bot@example.com"
    assert current["env"]["MCP_DEFAULT_AUTH"] == "microsoft"
    assert current["env"]["MCP_DISABLE_GROUPS"] == "raw"
    assert write_channel.call_args.kwargs["server_id"] == "friends"
    assert write_channel.call_args.kwargs["allowed_players"] == ["Alice"]
